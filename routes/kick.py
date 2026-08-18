import base64
import hashlib
import secrets
import time
from urllib.parse import urlencode

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa, ed25519
from flask import Blueprint, jsonify, redirect, request, session

from core.database import get_conn
from core.services import ensure_channel, ensure_player, get_channel, get_player, get_rank


kick_bp = Blueprint("kick", __name__)

KICK_API = "https://api.kick.com/public/v1"
KICK_ID = "https://id.kick.com"
KICK_PUBLIC_KEY_URL = f"{KICK_API}/public-key"

_oauth_states = {}
_public_key_pem = None


def _env(name, default=""):
    import os
    return os.environ.get(name, default).strip()


def _client_id():
    return _env("KICK_CLIENT_ID")


def _client_secret():
    return _env("KICK_CLIENT_SECRET")


def _redirect_uri():
    configured = _env("KICK_REDIRECT_URI")
    if configured:
        return configured
    proto = request.headers.get("X-Forwarded-Proto", request.scheme)
    return f"{proto}://{request.host}/kick/callback"


def _webhook_url():
    configured = _env("KICK_WEBHOOK_URL")
    if configured:
        return configured.rstrip("/")
    proto = request.headers.get("X-Forwarded-Proto", request.scheme)
    return f"{proto}://{request.host}/kick/webhook"


def _scopes():
    return "user:read chat:write events:subscribe"


def _pkce_pair():
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _save_connection(user, token_data):
    broadcaster_id = int(user.get("user_id"))
    now = int(time.time())
    expires_in = int(token_data.get("expires_in") or 0)
    expires_at = now + expires_in if expires_in else 0
    scope = str(token_data.get("scope") or "")
    username = str(user.get("username") or "")

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO kick_connections
                   (broadcaster_user_id, username, access_token, refresh_token, expires_at, scope, updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,NOW())
                   ON CONFLICT (broadcaster_user_id) DO UPDATE SET
                     username=EXCLUDED.username,
                     access_token=EXCLUDED.access_token,
                     refresh_token=COALESCE(EXCLUDED.refresh_token,kick_connections.refresh_token),
                     expires_at=EXCLUDED.expires_at,
                     scope=EXCLUDED.scope,
                     updated_at=NOW()""",
                (broadcaster_id, username, token_data.get("access_token"),
                 token_data.get("refresh_token"), expires_at, scope),
            )
        conn.commit()
    finally:
        conn.close()

    ensure_channel(broadcaster_id, username)
    return broadcaster_id


def _get_connection(broadcaster_id):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT broadcaster_user_id, username, access_token, refresh_token, expires_at, scope
                   FROM kick_connections WHERE broadcaster_user_id=%s""",
                (int(broadcaster_id),),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return {
        "broadcaster_user_id": row[0], "username": row[1],
        "access_token": row[2], "refresh_token": row[3],
        "expires_at": row[4] or 0, "scope": row[5] or "",
    }


def _update_tokens(broadcaster_id, token_data):
    now = int(time.time())
    expires_in = int(token_data.get("expires_in") or 0)
    expires_at = now + expires_in if expires_in else 0
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE kick_connections SET
                   access_token=%s, refresh_token=COALESCE(%s, refresh_token),
                   expires_at=%s, scope=%s, updated_at=NOW()
                   WHERE broadcaster_user_id=%s""",
                (token_data.get("access_token"), token_data.get("refresh_token"),
                 expires_at, str(token_data.get("scope") or ""), int(broadcaster_id)),
            )
        conn.commit()
    finally:
        conn.close()


def _refresh_connection(conn_data):
    refresh_token = conn_data.get("refresh_token")
    if not refresh_token or not _client_id() or not _client_secret():
        return None
    try:
        response = requests.post(
            f"{KICK_ID}/oauth/token",
            data={
                "grant_type": "refresh_token",
                "client_id": _client_id(),
                "client_secret": _client_secret(),
                "refresh_token": refresh_token,
            },
            timeout=20,
        )
        data = response.json()
    except Exception as exc:
        print(f"[KICK-OAUTH] erro ao renovar: {exc}", flush=True)
        return None
    if response.status_code >= 400 or not data.get("access_token"):
        print(f"[KICK-OAUTH] renovação falhou HTTP {response.status_code}: {data}", flush=True)
        return None
    _update_tokens(conn_data["broadcaster_user_id"], data)
    conn_data.update({
        "access_token": data.get("access_token"),
        "refresh_token": data.get("refresh_token") or conn_data.get("refresh_token"),
        "expires_at": int(time.time()) + int(data.get("expires_in") or 0),
        "scope": str(data.get("scope") or conn_data.get("scope") or ""),
    })
    return conn_data


def _valid_connection(broadcaster_id):
    conn_data = _get_connection(broadcaster_id)
    if not conn_data:
        return None
    if not conn_data.get("access_token"):
        return None
    if int(conn_data.get("expires_at") or 0) <= int(time.time()) + 60:
        return _refresh_connection(conn_data)
    return conn_data


def _kick_user(access_token):
    response = requests.get(
        f"{KICK_API}/users",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )
    data = response.json()
    if response.status_code >= 400:
        raise RuntimeError(f"Kick /users HTTP {response.status_code}: {data}")
    users = data.get("data") or []
    if not users:
        raise RuntimeError("Kick /users não retornou o usuário autenticado.")
    return users[0]


def _exchange_code(code, verifier):
    response = requests.post(
        f"{KICK_ID}/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": _client_id(),
            "client_secret": _client_secret(),
            "redirect_uri": _redirect_uri(),
            "code_verifier": verifier,
            "code": code,
        },
        timeout=20,
    )
    data = response.json()
    if response.status_code >= 400 or not data.get("access_token"):
        raise RuntimeError(f"Kick OAuth HTTP {response.status_code}: {data}")
    return data


def _subscribe_chat(access_token, broadcaster_id=None):
    # Recria a assinatura para eliminar uma assinatura antiga do Worker.
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    params = {}
    if broadcaster_id is not None:
        params["broadcaster_user_id"] = int(broadcaster_id)

    # 1) Lista as assinaturas atuais deste canal.
    response = requests.get(
        f"{KICK_API}/events/subscriptions",
        headers=headers,
        params=params,
        timeout=20,
    )

    try:
        data = response.json()
    except Exception:
        data = {"raw": response.text[:1000]}

    print(
        f"[KICK-EVENTS] subscriptions -> HTTP {response.status_code}: {data}",
        flush=True,
    )

    if response.status_code >= 400:
        raise RuntimeError(
            f"Falha ao consultar assinaturas Kick: HTTP "
            f"{response.status_code}: {data}"
        )

    existing = data.get("data") or []

    # 2) Remove assinaturas antigas de chat.message.sent.
    chat_ids = [
        str(item.get("id"))
        for item in existing
        if item.get("id")
        and str(item.get("event") or item.get("name") or "") == "chat.message.sent"
    ]

    if chat_ids:
        delete_response = requests.delete(
            f"{KICK_API}/events/subscriptions",
            headers=headers,
            params=[("id", subscription_id) for subscription_id in chat_ids],
            timeout=20,
        )

        try:
            delete_data = delete_response.json()
        except Exception:
            delete_data = delete_response.text[:1000]

        print(
            f"[KICK-EVENTS] delete chat subscriptions {chat_ids} -> "
            f"HTTP {delete_response.status_code}: {delete_data}",
            flush=True,
        )

        if delete_response.status_code >= 400:
            raise RuntimeError(
                f"Falha ao remover assinaturas antigas: HTTP "
                f"{delete_response.status_code}: {delete_data}"
            )

    # 3) Cria uma nova assinatura.
    # Com user access token, a Kick identifica o broadcaster pelo token.
    payload = {
        "broadcaster_user_id": int(broadcaster_id) if broadcaster_id is not None else None,
        "events": [{"name": "chat.message.sent", "version": 1}],
        "method": "webhook",
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    print(
        f"[KICK-EVENTS] criando assinatura para broadcaster={broadcaster_id} "
        f"webhook={_webhook_url()}",
        flush=True,
    )

    response = requests.post(
        f"{KICK_API}/events/subscriptions",
        headers=headers,
        json=payload,
        timeout=20,
    )

    try:
        data = response.json()
    except Exception:
        data = {"raw": response.text[:1000]}

    print(
        f"[KICK-EVENTS] CREATE chat.message.sent -> "
        f"HTTP {response.status_code}: {data}",
        flush=True,
    )

    if response.status_code >= 400:
        raise RuntimeError(
            f"Falha ao criar chat.message.sent: HTTP "
            f"{response.status_code}: {data}"
        )

    return {
        "ok": True,
        "already": False,
        "recreated": True,
        "deleted_subscription_ids": chat_ids,
        "data": data,
    }


def _fetch_public_key():
    # Busca e mantém em cache a chave RSA pública usada pela Kick.
    global _public_key_pem

    response = requests.get(
        KICK_PUBLIC_KEY_URL,
        headers={"Accept": "application/json"},
        timeout=20,
    )

    try:
        data = response.json()
    except Exception:
        data = {"raw": response.text[:1000]}

    print(
        f"[KICK-WEBHOOK] public-key -> HTTP {response.status_code}: "
        f"{data if response.status_code >= 400 else 'OK'}",
        flush=True,
    )

    response.raise_for_status()

    # A resposta atual da Kick é:
    # {"data": {"public_key": "-----BEGIN PUBLIC KEY-----..."},
    #  "message": "OK"}
    wrapper = data.get("data") if isinstance(data, dict) else None
    wrapper = wrapper if isinstance(wrapper, dict) else {}

    key = (
        wrapper.get("public_key")
        or wrapper.get("publicKey")
        or data.get("public_key")
        or data.get("publicKey")
    )

    if not isinstance(key, str) or "BEGIN PUBLIC KEY" not in key:
        raise RuntimeError(
            "Kick retornou HTTP 200, mas a chave pública não foi encontrada "
            f"na resposta: {data}"
        )

    _public_key_pem = key.strip()
    return _public_key_pem


def _verify_signature(raw_body, message_id, timestamp, signature):
    global _public_key_pem

    if not message_id or not timestamp or not signature:
        print("[KICK-WEBHOOK] headers de assinatura incompletos", flush=True)
        return False

    # A Kick assina exatamente:
    # Kick-Event-Message-Id.Kick-Event-Message-Timestamp.raw_body
    signed_message = (
        f"{message_id}.{timestamp}.".encode("utf-8") + raw_body
    )

    for attempt in range(2):
        try:
            if not _public_key_pem:
                _fetch_public_key()

            public_key = serialization.load_pem_public_key(
                _public_key_pem.encode("utf-8")
            )
            signature_bytes = base64.b64decode(signature, validate=True)

            if not isinstance(public_key, rsa.RSAPublicKey):
                raise RuntimeError(
                    f"Tipo de chave Kick não suportado: "
                    f"{type(public_key).__name__}"
                )

            public_key.verify(
                signature_bytes,
                signed_message,
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
            return True

        except Exception as exc:
            print(
                f"[KICK-WEBHOOK] assinatura inválida/tentativa "
                f"{attempt + 1}: {exc}",
                flush=True,
            )
            _public_key_pem = None

    return False


def _remember_event(message_id):
    if not message_id:
        return True
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO kick_webhook_events (message_id, event_type)
                   VALUES (%s,%s) ON CONFLICT (message_id) DO NOTHING""",
                (message_id, request.headers.get("Kick-Event-Type", "")),
            )
            inserted = cur.rowcount == 1
        conn.commit()
        return inserted
    finally:
        conn.close()


def _send_chat(broadcaster_id, content):
    conn_data = _valid_connection(broadcaster_id)
    if not conn_data:
        print(f"[KICK-CHAT] sem conexão para broadcaster={broadcaster_id}", flush=True)
        return False
    text = str(content or "").strip()[:500]
    if not text:
        return False
    response = requests.post(
        f"{KICK_API}/chat",
        headers={
            "Authorization": f"Bearer {conn_data['access_token']}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json={
            "content": text,
            "type": "user",
            "broadcaster_user_id": int(broadcaster_id),
        },
        timeout=20,
    )
    try:
        data = response.json()
    except Exception:
        data = response.text[:500]
    print(f"[KICK-CHAT] HTTP {response.status_code}: {data}", flush=True)
    return response.status_code < 400


def _format_balance(broadcaster_id, username):
    channel = get_channel(broadcaster_id)
    player = get_player(broadcaster_id, username)
    rank = get_rank(broadcaster_id, username)
    return (
        f"{username}, você tem {player['points']} {channel['currency_name']}. "
        f"{channel['currency_emoji']} Sua posição no ranking é #{rank}."
    )


def _format_ranking(broadcaster_id):
    channel = get_channel(broadcaster_id)
    limit = max(1, min(int(channel["rank_limit"]), 10))
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT username, points FROM players
                   WHERE broadcaster_user_id=%s
                   ORDER BY points DESC, username ASC LIMIT %s""",
                (int(broadcaster_id), limit),
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    if not rows:
        return f"🏆 Ranking: ninguém possui {channel['currency_name']} ainda."
    parts = [f"{i}. {name} {points}" for i, (name, points) in enumerate(rows, 1)]
    return f"🏆 Ranking: " + " • ".join(parts)


def _custom_command(broadcaster_id, command, username):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT response FROM custom_commands WHERE broadcaster_user_id=%s AND command=%s",
                (int(broadcaster_id), command),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return str(row[0]).replace("$(user)", username).replace("{user}", username)


def _process_chat(payload):
    broadcaster = payload.get("broadcaster") or {}
    sender = payload.get("sender") or {}
    try:
        broadcaster_id = int(broadcaster.get("user_id"))
        user_id = int(sender.get("user_id")) if sender.get("user_id") else None
    except (TypeError, ValueError):
        print("[KICK-CHAT] payload sem IDs válidos", flush=True)
        return
    username = str(sender.get("username") or "").strip()
    content = str(payload.get("content") or "").strip()
    if not broadcaster_id or not username:
        return

    ensure_channel(broadcaster_id, str(broadcaster.get("username") or ""))
    ensure_player(broadcaster_id, username, user_id)

    if not content.startswith("!"):
        return

    pieces = content.split()
    command = pieces[0].lower()

    try:
        channel = get_channel(broadcaster_id)
        if command == str(channel["currency_command"]).lower():
            _send_chat(broadcaster_id, _format_balance(broadcaster_id, username))
            return

        if command in {"!ranking", "!rank"}:
            _send_chat(broadcaster_id, _format_ranking(broadcaster_id))
            return

        if command == "!duelo":
            if len(pieces) < 2:
                _send_chat(broadcaster_id, f"⚔️ Use !duelo @usuário")
                return
            defender = pieces[1].lstrip("@").strip()
            if not defender or defender.lower() == username.lower():
                _send_chat(broadcaster_id, "⚔️ Você não pode duelar consigo mesmo.")
                return
            ensure_player(broadcaster_id, defender)
            import random
            winner = random.choice([username, defender])
            loser = defender if winner == username else username
            win = int(channel["duel_win_points"])
            loss = int(channel["duel_loss_points"])
            conn = get_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE players SET points=points+%s, duels=duels+1, streak=streak+1, updated_at=NOW()
                           WHERE broadcaster_user_id=%s AND username=%s""",
                        (win, broadcaster_id, winner),
                    )
                    cur.execute(
                        """UPDATE players SET points=GREATEST(0,points-%s), duels=duels+1, streak=0, updated_at=NOW()
                           WHERE broadcaster_user_id=%s AND username=%s""",
                        (loss, broadcaster_id, loser),
                    )
                    cur.execute(
                        """INSERT INTO duel_events
                           (broadcaster_user_id, attacker, defender, winner, winner_points_delta, loser_points_delta)
                           VALUES (%s,%s,%s,%s,%s,%s)""",
                        (broadcaster_id, username, defender, winner, win, -loss),
                    )
                conn.commit()
            finally:
                conn.close()
            _send_chat(
                broadcaster_id,
                f"⚔️ {username} desafiou {defender}! 🏆 {winner} venceu! +{win} {channel['currency_name']} para {winner} e -{loss} para {loser}.",
            )
            return

        custom = _custom_command(broadcaster_id, command, username)
        if custom is not None:
            _send_chat(broadcaster_id, custom)
    except Exception as exc:
        print(f"[KICK-CHAT] erro processando {content!r}: {exc}", flush=True)


def _process_webhook(payload, event_type):
    print(f"[KICK-WEBHOOK] evento recebido para processamento: {event_type}", flush=True)
    if event_type == "chat.message.sent":
        print("[KICK-WEBHOOK] processando chat.message.sent", flush=True)
        _process_chat(payload)


@kick_bp.get("/login")
def login():
    if not _client_id() or not _client_secret():
        return jsonify({"ok": False, "error": "KICK_CLIENT_ID/KICK_CLIENT_SECRET não configurados no Render."}), 503
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(32)
    session["kick_oauth_state"] = state
    session["kick_code_verifier"] = verifier
    params = {
        "response_type": "code",
        "client_id": _client_id(),
        "redirect_uri": _redirect_uri(),
        "scope": _scopes(),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    return redirect(f"{KICK_ID}/oauth/authorize?{urlencode(params)}")


@kick_bp.get("/callback")
def callback():
    error = request.args.get("error")
    if error:
        return jsonify({"ok": False, "error": error, "description": request.args.get("error_description", "")}), 400
    state = request.args.get("state", "")
    expected = session.pop("kick_oauth_state", "")
    verifier = session.pop("kick_code_verifier", "")
    if not state or state != expected or not verifier:
        return jsonify({"ok": False, "error": "OAuth state inválido ou expirado."}), 400
    try:
        token_data = _exchange_code(request.args.get("code", ""), verifier)
        user = _kick_user(token_data["access_token"])
        broadcaster_id = _save_connection(user, token_data)
        subscription = _subscribe_chat(token_data["access_token"], broadcaster_id)
        return jsonify({
            "ok": True,
            "message": "Kick conectado ao SN7 Core.",
            "broadcaster_user_id": broadcaster_id,
            "username": user.get("username"),
            "chat_subscription": subscription,
            "webhook": _webhook_url(),
        })
    except Exception as exc:
        print(f"[KICK-OAUTH] callback falhou: {exc}", flush=True)
        return jsonify({"ok": False, "error": str(exc)}), 500


@kick_bp.get("/status")
def status():
    try:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT broadcaster_user_id, username, expires_at, scope, updated_at
                       FROM kick_connections ORDER BY broadcaster_user_id"""
                )
                rows = [
                    {"broadcaster_user_id": r[0], "username": r[1], "expires_at": r[2], "scope": r[3], "updated_at": r[4].isoformat()}
                    for r in cur.fetchall()
                ]
        finally:
            conn.close()
        return jsonify({"ok": True, "connections": rows, "webhook": _webhook_url()})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@kick_bp.post("/subscribe")
def subscribe():
    broadcaster_id = request.args.get("broadcaster_id", "")
    if not broadcaster_id.isdigit():
        return jsonify({"ok": False, "error": "broadcaster_id inválido"}), 400
    conn_data = _valid_connection(int(broadcaster_id))
    if not conn_data:
        return jsonify({"ok": False, "error": "Canal não conectado ao Core."}), 404
    try:
        result = _subscribe_chat(conn_data["access_token"], int(broadcaster_id))
        return jsonify(result)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@kick_bp.post("/webhook")
def webhook():
    raw_body = request.get_data(cache=True)
    message_id = request.headers.get("Kick-Event-Message-Id", "")
    timestamp = request.headers.get("Kick-Event-Message-Timestamp", "")
    signature = request.headers.get("Kick-Event-Signature", "")
    event_type = request.headers.get("Kick-Event-Type", "")
    print(f"[KICK-WEBHOOK] recebido type={event_type} message_id={message_id}", flush=True)
    if not _verify_signature(raw_body, message_id, timestamp, signature):
        return jsonify({"ok": False, "error": "assinatura Kick inválida"}), 401

    try:
        if not _remember_event(message_id):
            return jsonify({"ok": True, "duplicate": True})
        payload = request.get_json(silent=True) or {}
        # Responde rápido para a Kick; o processamento da economia/comando ocorre
        # em background e não prende o webhook durante cold start do banco.
        import threading
        threading.Thread(
            target=_process_webhook,
            args=(payload, event_type),
            daemon=True,
            name="kick-event",
        ).start()
        return jsonify({"ok": True})
    except Exception as exc:
        print(f"[KICK-WEBHOOK] erro aceitando evento: {exc}", flush=True)
        return jsonify({"ok": False, "error": "falha interna"}), 500
