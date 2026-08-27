import hashlib
import hmac
import os
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
import time
from urllib.parse import urlencode

import requests
from flask import Blueprint, jsonify, redirect, request, session

from core.auth import get_session_broadcaster_id, require_session_broadcaster, stable_channel_id
from core.database import get_conn
from core.services import award_watch_presence, add_points, get_point_rewards
from routes.kick import _process_chat

twitch_bp = Blueprint("twitch", __name__)

TWITCH_API = "https://api.twitch.tv/helix"
TWITCH_OAUTH = "https://id.twitch.tv/oauth2"
EVENTSUB_TYPE = "channel.chat.message"
EVENTSUB_VERSION = "1"
EVENTSUB_REWARD_TYPES = (
    ("channel.chat.message", "1"),
    ("channel.subscribe", "1"),
    ("channel.cheer", "1"),
)

# EventSub deve receber 204 rapidamente. O processamento do comando e a
# resposta no chat rodam fora da requisição HTTP, evitando que a Twitch fique
# esperando PostgreSQL/Twitch API e reduzindo reentregas.
_chat_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="sn7-twitch-chat")
_reward_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sn7-twitch-reward")
_conn_cache = {}
_conn_cache_ttl = 30.0
_conn_cache_lock = threading.RLock()
# EventSub deve ser processado de forma idempotente. Em caso de inscrições
# duplicadas ou reentrega da Twitch, o mesmo message_id não pode executar o
# comando duas vezes. Mantemos uma janela curta em memória por processo.
_EVENTSUB_DEDUP_TTL = 120
_eventsub_seen = {}
_eventsub_seen_lock = threading.Lock()


def _eventsub_duplicate(message_id):
    message_id = str(message_id or "").strip()
    if not message_id:
        return False

    now = time.time()
    with _eventsub_seen_lock:
        expired = [key for key, seen_at in _eventsub_seen.items() if now - seen_at > _EVENTSUB_DEDUP_TTL]
        for key in expired:
            _eventsub_seen.pop(key, None)

        if message_id in _eventsub_seen:
            return True

        _eventsub_seen[message_id] = now
        return False


def _env(name, default=""):
    return str(os.environ.get(name, default) or "").strip()


def _cfg():
    return _env("TWITCH_CLIENT_ID"), _env("TWITCH_CLIENT_SECRET")


def _redirect_uri():
    configured = _env("TWITCH_REDIRECT_URI")
    if configured:
        return configured
    return f"{_env('SN7_PUBLIC_URL', 'https://sn7-core.onrender.com').rstrip('/')}/twitch/callback"


def _eventsub_callback():
    configured = _env("TWITCH_EVENTSUB_CALLBACK")
    if configured:
        return configured
    return f"{_env('SN7_PUBLIC_URL', 'https://sn7-core.onrender.com').rstrip('/')}/twitch/eventsub"


def _eventsub_secret():
    return _env("TWITCH_EVENTSUB_SECRET")


def _configured():
    cid, secret = _cfg()
    return bool(cid and secret and _eventsub_secret())


def _conn(bid):
    bid = int(bid)
    now = time.monotonic()
    with _conn_cache_lock:
        cached = _conn_cache.get(bid)
        if cached and cached["expires_at"] > now:
            return dict(cached["value"])

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT broadcaster_user_id,external_user_id,username,display_name,
                       profile_url,avatar_url,access_token,refresh_token,expires_at,
                       scope,bot_active
                  FROM chat_connections
                 WHERE provider='twitch'
                   AND (broadcaster_user_id=%s OR external_user_id=%s)
                 ORDER BY CASE WHEN broadcaster_user_id=%s THEN 0 ELSE 1 END
                 LIMIT 1
                """,
                (int(bid), str(bid), int(bid)),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        return None

    value = {
        "broadcaster_user_id": int(row[0]),
        "external_user_id": str(row[1] or ""),
        "username": str(row[2] or ""),
        "display_name": str(row[3] or ""),
        "profile_url": str(row[4] or ""),
        "avatar_url": str(row[5] or ""),
        "access_token": row[6],
        "refresh_token": row[7],
        "expires_at": int(row[8] or 0),
        "scope": row[9] or "",
        "bot_active": bool(row[10]),
    }
    with _conn_cache_lock:
        _conn_cache[bid] = {"value": dict(value), "expires_at": time.monotonic() + _conn_cache_ttl}
    return value


def _forget_conn_cache(bid):
    with _conn_cache_lock:
        _conn_cache.pop(int(bid), None)


def _refresh(conn):
    if not conn:
        return None
    if int(conn.get("expires_at") or 0) > int(time.time()) + 60:
        return conn

    cid, secret = _cfg()
    refresh_token = str(conn.get("refresh_token") or "").strip()
    if not cid or not secret or not refresh_token:
        raise RuntimeError("A sessão da Twitch expirou. Conecte a Twitch novamente.")

    response = requests.post(
        f"{TWITCH_OAUTH}/token",
        params={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": cid,
            "client_secret": secret,
        },
        timeout=15,
    )
    data = response.json()

    if response.status_code >= 400 or not data.get("access_token"):
        message = data.get("message") or data.get("error_description") or data.get("error")
        raise RuntimeError(message or "Não foi possível renovar a sessão da Twitch.")

    conn["access_token"] = data["access_token"]
    conn["refresh_token"] = data.get("refresh_token") or refresh_token
    conn["expires_at"] = int(time.time()) + int(data.get("expires_in") or 14400)
    if data.get("scope"):
        conn["scope"] = " ".join(data["scope"]) if isinstance(data["scope"], list) else str(data["scope"])

    db = get_conn()
    try:
        with db.cursor() as cur:
            cur.execute(
                """
                UPDATE chat_connections
                   SET access_token=%s,refresh_token=%s,expires_at=%s,
                       scope=%s,updated_at=NOW()
                 WHERE broadcaster_user_id=%s AND provider='twitch'
                """,
                (
                    conn["access_token"],
                    conn["refresh_token"],
                    conn["expires_at"],
                    conn["scope"],
                    conn["broadcaster_user_id"],
                ),
            )
        db.commit()
    finally:
        db.close()

    return conn


def _save_connection(bid, token, user):
    expires_at = int(time.time()) + int(token.get("expires_in") or 14400)
    scope = token.get("scope") or ""
    if isinstance(scope, list):
        scope = " ".join(scope)

    db = get_conn()
    try:
        with db.cursor() as cur:
            cur.execute(
                """
                INSERT INTO chat_connections
                    (broadcaster_user_id,provider,external_user_id,username,
                     display_name,profile_url,avatar_url,access_token,
                     refresh_token,expires_at,scope,bot_active,cursor,updated_at)
                VALUES
                    (%s,'twitch',%s,%s,%s,%s,%s,%s,%s,%s,%s,FALSE,'',NOW())
                ON CONFLICT (broadcaster_user_id,provider) DO UPDATE SET
                    external_user_id=EXCLUDED.external_user_id,
                    username=EXCLUDED.username,
                    display_name=EXCLUDED.display_name,
                    profile_url=EXCLUDED.profile_url,
                    avatar_url=EXCLUDED.avatar_url,
                    access_token=EXCLUDED.access_token,
                    refresh_token=COALESCE(EXCLUDED.refresh_token,chat_connections.refresh_token),
                    expires_at=EXCLUDED.expires_at,
                    scope=EXCLUDED.scope,
                    bot_active=FALSE,
                    cursor='',
                    updated_at=NOW()
                """,
                (
                    int(bid),
                    str(user.get("id") or ""),
                    str(user.get("login") or ""),
                    str(user.get("display_name") or ""),
                    f"https://twitch.tv/{user.get('login') or ''}",
                    str(user.get("profile_image_url") or ""),
                    token.get("access_token"),
                    token.get("refresh_token"),
                    expires_at,
                    str(scope),
                ),
            )
        db.commit()
    finally:
        db.close()


def _token_exchange(code):
    code = str(code or "").strip()
    if not code:
        raise RuntimeError("A Twitch não retornou o código de autorização.")

    cid, secret = _cfg()
    response = requests.post(
        f"{TWITCH_OAUTH}/token",
        data={
            "client_id": cid,
            "client_secret": secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": _redirect_uri(),
        },
        timeout=15,
    )
    data = response.json()

    if response.status_code >= 400 or not data.get("access_token"):
        raise RuntimeError(
            data.get("message")
            or data.get("error_description")
            or data.get("error")
            or "OAuth da Twitch recusado."
        )
    return data


def _user(access_token):
    cid, _ = _cfg()
    response = requests.get(
        f"{TWITCH_API}/users",
        headers={"Client-Id": cid, "Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    data = response.json()

    if response.status_code >= 400 or not data.get("data"):
        raise RuntimeError(
            (data.get("message") or "A Twitch não retornou o usuário autenticado.")
        )
    return data["data"][0]


def _app_token():
    cid, secret = _cfg()
    response = requests.post(
        f"{TWITCH_OAUTH}/token",
        params={
            "client_id": cid,
            "client_secret": secret,
            "grant_type": "client_credentials",
        },
        timeout=15,
    )
    data = response.json()

    if response.status_code >= 400 or not data.get("access_token"):
        raise RuntimeError(
            data.get("message") or "Não foi possível obter o token da aplicação Twitch."
        )
    return data["access_token"]


def _eventsub_headers(access_token):
    cid, _ = _cfg()
    return {
        "Client-Id": cid,
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }


def _list_eventsub(access_token):
    response = requests.get(
        f"{TWITCH_API}/eventsub/subscriptions",
        headers=_eventsub_headers(access_token),
        timeout=15,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"Twitch EventSub HTTP {response.status_code}: "
            f"{response.text[:400]}"
        )
    return response.json().get("data") or []


def _subscription_matches(sub, external_user_id, sub_type=None):
    condition = sub.get("condition") or {}
    wanted_type = sub_type or EVENTSUB_TYPE
    if sub.get("type") != wanted_type:
        return False
    if sub.get("version") != "1":
        return False
    if condition.get("broadcaster_user_id") != str(external_user_id):
        return False
    if wanted_type == "channel.chat.message":
        return condition.get("user_id") == str(external_user_id)
    return True


def _create_eventsub(app_token, external_user_id, secret, sub_type, version="1"):
    condition = {"broadcaster_user_id": str(external_user_id)}
    if sub_type == "channel.chat.message":
        condition["user_id"] = str(external_user_id)

    payload = {
        "type": sub_type,
        "version": version,
        "condition": condition,
        "transport": {
            "method": "webhook",
            "callback": _eventsub_callback(),
            "secret": secret,
        },
    }
    response = requests.post(
        f"{TWITCH_API}/eventsub/subscriptions",
        headers=_eventsub_headers(app_token),
        json=payload,
        timeout=15,
    )
    data = response.json()
    if response.status_code >= 400:
        message = (
            data.get("message")
            or (data.get("error") if isinstance(data.get("error"), str) else None)
            or f"Twitch EventSub HTTP {response.status_code}"
        )
        raise RuntimeError(f"{sub_type}: {message}")
    row = (data.get("data") or [{}])[0]
    return {"type": sub_type, "id": row.get("id"), "status": row.get("status")}


def _subscribe(conn):
    secret = _eventsub_secret()
    if not secret:
        raise RuntimeError("TWITCH_EVENTSUB_SECRET não configurado no Render.")
    if len(secret) < 10 or len(secret) > 100:
        raise RuntimeError("TWITCH_EVENTSUB_SECRET precisa ter entre 10 e 100 caracteres.")

    user_token = str(conn.get("access_token") or "").strip()
    if not user_token:
        raise RuntimeError("A sessão da Twitch não possui access token. Conecte a Twitch novamente.")

    granted = {part.strip() for part in str(conn.get("scope") or "").split() if part.strip()}
    chat_required = {"user:read:chat", "user:write:chat", "user:bot", "channel:bot"}
    missing_chat = sorted(chat_required - granted)
    if missing_chat:
        raise RuntimeError(
            "A autorização da Twitch está incompleta para o chat. "
            "Conecte a Twitch novamente para conceder: " + ", ".join(missing_chat)
        )

    reward_scope_missing = sorted({"channel:read:subscriptions", "bits:read"} - granted)

    app_token = _app_token()
    subscriptions = _list_eventsub(app_token)
    results = []

    for sub_type, version in EVENTSUB_REWARD_TYPES:
        if sub_type == "channel.subscribe" and "channel:read:subscriptions" in reward_scope_missing:
            results.append({
                "type": sub_type,
                "status": "scope_missing",
                "message": "Reconecte a Twitch para liberar channel:read:subscriptions.",
            })
            continue
        if sub_type == "channel.cheer" and "bits:read" in reward_scope_missing:
            results.append({
                "type": sub_type,
                "status": "scope_missing",
                "message": "Reconecte a Twitch para liberar bits:read.",
            })
            continue
        existing = next(
            (
                sub for sub in subscriptions
                if _subscription_matches(sub, conn["external_user_id"], sub_type)
                and sub.get("status") in {"enabled", "webhook_callback_verification_pending"}
            ),
            None,
        )
        if existing:
            results.append({
                "type": sub_type,
                "id": existing.get("id"),
                "status": existing.get("status"),
                "existing": True,
            })
            continue

        results.append(_create_eventsub(
            app_token, conn["external_user_id"], secret, sub_type, version
        ))

    return {"ok": True, "subscriptions": results}


def _unsubscribe(conn):
    try:
        app_token = _app_token()
        for sub in _list_eventsub(app_token):
            if not any(_subscription_matches(sub, conn["external_user_id"], typ) for typ, _ in EVENTSUB_REWARD_TYPES):
                continue
            sub_id = sub.get("id")
            if not sub_id:
                continue
            response = requests.delete(
                f"{TWITCH_API}/eventsub/subscriptions",
                headers=_eventsub_headers(app_token),
                params={"id": sub_id},
                timeout=15,
            )
            if response.status_code >= 400:
                print(
                    f"[TWITCH-EVENTSUB] unsubscribe HTTP {response.status_code}: "
                    f"{response.text[:300]}",
                    flush=True,
                )
    except Exception as exc:
        print(f"[TWITCH-EVENTSUB] unsubscribe falhou: {exc}", flush=True)


def _send_chat(conn, message):
    cid, _ = _cfg()
    response = requests.post(
        f"{TWITCH_API}/chat/messages",
        headers={
            "Client-Id": cid,
            "Authorization": f"Bearer {conn['access_token']}",
            "Content-Type": "application/json",
        },
        json={
            "broadcaster_id": str(conn["external_user_id"]),
            "sender_id": str(conn["external_user_id"]),
            "message": str(message)[:500],
        },
        timeout=10,
    )
    if response.status_code >= 400:
        print(
            f"[TWITCH-CHAT] send HTTP {response.status_code}: "
            f"{response.text[:500]}",
            flush=True,
        )


def _oauth_error(message, status=400):
    # Em navegação normal, devolve uma página simples em vez de um JSON
    # difícil de interpretar no celular.
    if "text/html" in str(request.headers.get("Accept") or ""):
        safe = (
            str(message)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )
        return (
            """<!doctype html><html lang="pt-BR"><meta charset="utf-8">
<title>SN7 • Twitch</title>
<style>
body{margin:0;background:#0b0d12;color:#e8ebf2;font:16px system-ui;
display:grid;place-items:center;min-height:100vh;padding:24px}
main{max-width:560px;background:#121720;border:1px solid #2a3240;
border-radius:18px;padding:24px}
a{display:inline-block;margin:10px 8px 0 0;color:#fff;background:#1c2430;
border:1px solid #354052;border-radius:10px;padding:10px 14px;
text-decoration:none;font-weight:700}
</style><main><h2>Conexão da Twitch não concluída</h2>
<p>__MESSAGE__</p><a href="/twitch/login">Tentar novamente</a>
<a href="/?profile=1">Voltar ao perfil</a></main></html>"""
            .replace("__MESSAGE__", safe)
        ), status
    return jsonify({"ok": False, "error": str(message)}), status


@twitch_bp.get("/login")
def login():
    # A Twitch pode ser a primeira plataforma da conta. Quando ainda não
    # existe um canal SN7, o callback cria um ID temporário estável.
    bid = get_session_broadcaster_id(validate=False)

    cid, secret = _cfg()
    if not cid or not secret:
        return _oauth_error(
            "TWITCH_CLIENT_ID/TWITCH_CLIENT_SECRET não configurados no Render.", 503
        )

    state = secrets.token_urlsafe(32)
    store_channel = str(request.args.get("channel") or "").strip()
    store_viewer = bool(request.args.get("store") == "1" and store_channel)
    session["twitch_oauth"] = {
        "state": state,
        "broadcaster_id": int(bid) if bid is not None else None,
        "created_at": int(time.time()),
        "purpose": "store_viewer" if store_viewer else "profile",
        "return_to": f"/loja/{store_channel}" if store_viewer else "/dashboard?profile=1",
    }

    params = {
        "client_id": cid,
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": "user:read" if store_viewer else "user:read:chat user:write:chat user:bot channel:bot",
        "state": state,
    }
    return redirect(f"{TWITCH_OAUTH}/authorize?{urlencode(params)}")


@twitch_bp.get("/callback")
def callback():
    oauth = session.pop("twitch_oauth", None)
    age = int(time.time()) - int((oauth or {}).get("created_at") or 0)
    state = str(request.args.get("state") or "")

    if (
        not oauth
        or age > 600
        or not state
        or not secrets.compare_digest(state, str(oauth.get("state") or ""))
    ):
        return _oauth_error(
            "A sessão do OAuth da Twitch expirou. Inicie a conexão novamente."
        )

    if request.args.get("error"):
        return _oauth_error(
            request.args.get("error_description")
            or request.args.get("error")
            or "A Twitch recusou a autorização."
        )

    try:
        token = _token_exchange(request.args.get("code"))
        user = _user(token["access_token"])

        if oauth.get("purpose") == "store_viewer":
            store_channel = str(oauth.get("return_to") or "/loja").strip()
            if not store_channel.startswith("/loja/"):
                store_channel = "/loja"
            session["sn7_store_viewer"] = {
                "platform": "twitch",
                "external_user_id": str(user.get("id") or ""),
                "username": str(user.get("login") or user.get("display_name") or "Twitch").strip(),
                "profile_picture_url": str(user.get("profile_image_url") or "").strip(),
            }
            session.permanent = True
            return redirect(store_channel)

        bid = oauth.get("broadcaster_id")
        if bid is None:
            current_profile = get_session_broadcaster_id(validate=False)
            bid = int(current_profile) if current_profile is not None else stable_channel_id("twitch", user.get("id"))
        else:
            require_session_broadcaster(bid)

        # A conta Twitch autenticada é a identidade usada pelo EventSub.
        _save_connection(int(bid), token, user)
        session["sn7_broadcaster_id"] = int(bid)
        # Mantemos a chave antiga para compatibilidade com rotas legadas.
        session["kick_broadcaster_id"] = int(bid)
        session.permanent = True
        return redirect("/dashboard?profile=1&twitch_connected=1")
    except Exception as exc:
        print(f"[TWITCH-OAUTH] callback falhou: {exc}", flush=True)
        return _oauth_error(str(exc), 502)


@twitch_bp.get("/<int:bid>/status")
def status(bid):
    try:
        require_session_broadcaster(bid)
    except PermissionError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 403

    try:
        conn = _conn(bid)
        if conn and int(conn.get("expires_at") or 0) <= int(time.time()) + 60:
            conn = _refresh(conn)

        return jsonify(
            {
                "ok": True,
                "configured": _configured(),
                "connected": bool(conn),
                "active": bool(conn and conn["bot_active"]),
                "token_expired": bool(
                    conn
                    and int(conn.get("expires_at") or 0) <= int(time.time()) + 60
                ),
                "user": (
                    {
                        "id": conn["external_user_id"],
                        "username": conn["username"],
                        "display_name": conn["display_name"],
                        "avatar_url": conn["avatar_url"],
                    }
                    if conn
                    else None
                ),
            }
        )
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502


@twitch_bp.post("/<int:bid>/bot/toggle")
def toggle(bid):
    try:
        require_session_broadcaster(bid)
    except PermissionError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 403

    payload = request.get_json(silent=True) or {}
    desired = payload.get("active")
    if not isinstance(desired, bool):
        return jsonify({"ok": False, "error": "active precisa ser true ou false."}), 400

    try:
        conn = _refresh(_conn(bid))
        if not conn:
            return jsonify({"ok": False, "error": "Conecte a Twitch primeiro."}), 403

        if desired:
            if not _configured():
                return jsonify(
                    {
                        "ok": False,
                        "error": "Configure TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET e TWITCH_EVENTSUB_SECRET no Render.",
                    }
                ), 503
            result = _subscribe(conn)
        else:
            result = _unsubscribe(conn)

        db = get_conn()
        try:
            with db.cursor() as cur:
                cur.execute(
                    """
                    UPDATE chat_connections
                       SET bot_active=%s,updated_at=NOW()
                     WHERE broadcaster_user_id=%s AND provider='twitch'
                    """,
                    (desired, int(bid)),
                )
            db.commit()
        finally:
            db.close()
        _forget_conn_cache(bid)

        return jsonify({"ok": True, "active": desired, "eventsub": result})
    except Exception as exc:
        message = str(exc)
        print(f"[TWITCH-BOT] toggle falhou broadcaster={bid}: {message}", flush=True)

        # Evita deixar o banco marcado como ativo quando a Twitch recusou
        # a criação do EventSub.
        if desired:
            try:
                db = get_conn()
                try:
                    with db.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE chat_connections
                               SET bot_active=FALSE,updated_at=NOW()
                             WHERE broadcaster_user_id=%s AND provider='twitch'
                            """,
                            (int(bid),),
                        )
                    db.commit()
                finally:
                    db.close()
            except Exception as db_exc:
                print(f"[TWITCH-BOT] rollback bot_active falhou: {db_exc}", flush=True)

        return jsonify({"ok": False, "error": message}), 502


@twitch_bp.post("/<int:bid>/disconnect")
def disconnect(bid):
    try:
        require_session_broadcaster(bid)
    except PermissionError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 403

    try:
        conn = _conn(bid)
        if conn:
            _unsubscribe(conn)
        db = get_conn()
        try:
            with db.cursor() as cur:
                cur.execute(
                    "DELETE FROM chat_connections WHERE broadcaster_user_id=%s AND provider='twitch'",
                    (int(bid),),
                )
            db.commit()
        finally:
            db.close()
        _forget_conn_cache(bid)
        return jsonify({"ok": True, "connected": False, "active": False})
    except Exception as exc:
        print(f"[TWITCH-OAUTH] disconnect falhou broadcaster={bid}: {exc}", flush=True)
        return jsonify({"ok": False, "error": str(exc)}), 502


@twitch_bp.post("/eventsub")
def eventsub():
    secret = _eventsub_secret()
    message_id = request.headers.get("Twitch-Eventsub-Message-Id", "")
    timestamp = request.headers.get("Twitch-Eventsub-Message-Timestamp", "")
    signature = request.headers.get("Twitch-Eventsub-Message-Signature", "")
    body = request.get_data()

    if not secret or not message_id or not timestamp or not signature:
        return ("", 403)

    # Twitch exige a assinatura HMAC do corpo bruto. Nunca use request.json
    # para calcular a assinatura.
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        (message_id + timestamp).encode("utf-8") + body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, signature):
        return ("", 403)

    # A Twitch pode reenviar uma notificação ou haver inscrições antigas
    # duplicadas. O Message-Id é único por evento e permite garantir que um
    # comando seja executado apenas uma vez.
    if _eventsub_duplicate(message_id):
        print(f"[TWITCH-EVENTSUB] evento duplicado ignorado: {message_id}", flush=True)
        return ("", 204)

    message_type = request.headers.get("Twitch-Eventsub-Message-Type", "")
    payload = request.get_json(silent=True) or {}

    if message_type == "webhook_callback_verification":
        challenge = str(payload.get("challenge") or "")
        return (challenge, 200, {"Content-Type": "text/plain; charset=utf-8"})

    if message_type == "revocation":
        subscription = payload.get("subscription") or {}
        condition = subscription.get("condition") or {}
        external_id = str(condition.get("broadcaster_user_id") or "")
        if external_id:
            db = get_conn()
            try:
                with db.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE chat_connections
                           SET bot_active=FALSE,updated_at=NOW()
                         WHERE provider='twitch' AND external_user_id=%s
                        """,
                        (external_id,),
                    )
                db.commit()
            finally:
                db.close()
        return ("", 204)

    if message_type != "notification":
        return ("", 204)

    subscription = payload.get("subscription") or {}
    event_type = str(subscription.get("type") or "channel.chat.message")
    event = payload.get("event") or {}
    try:
        bid = int(event.get("broadcaster_user_id") or 0)
    except (TypeError, ValueError):
        return ("", 204)

    if not bid:
        return ("", 204)

    # Tudo que pode bloquear (PostgreSQL, refresh de token, motor de comando e
    # envio da resposta) fica fora do request do EventSub.
    try:
        _chat_executor.submit(_process_twitch_event, bid, event, event_type)
    except Exception as exc:
        print(f"[TWITCH-EVENTSUB] fila de processamento falhou: {exc}", flush=True)

    return ("", 204)


def _process_twitch_event(bid, event, event_type="channel.chat.message"):
    try:
        conn = _refresh(_conn(bid))
        if not conn or not conn["bot_active"]:
            return

        username = str(
            event.get("user_login")
            or event.get("user_name")
            or event.get("chatter_user_login")
            or event.get("chatter_user_name")
            or ""
        ).strip()
        uid = event.get("user_id") or event.get("chatter_user_id")

        # Recompensas de assinatura e Bits são processadas fora do request
        # HTTP do EventSub, então a Twitch continua recebendo 204 imediatamente.
        if event_type == "channel.subscribe":
            rewards = get_point_rewards(bid)
            bonus = int(rewards.get("sub_bonus") or 0)
            if username and bonus > 0:
                add_points(bid, username, bonus, uid, "twitch")
                print(
                    f"[SN7-REWARDS] Twitch {username} +{bonus} por sub "
                    f"(tier {event.get('tier') or '?'})",
                    flush=True,
                )
            return

        if event_type == "channel.cheer":
            try:
                bits = max(0, int(event.get("bits") or 0))
            except (TypeError, ValueError):
                bits = 0
            rewards = get_point_rewards(bid)
            bonus = bits * int(rewards.get("bits_bonus_per_bit") or 0)
            if username and bonus > 0:
                add_points(bid, username, bonus, uid, "twitch")
                print(
                    f"[SN7-REWARDS] Twitch {username} +{bonus} por {bits} Bits",
                    flush=True,
                )
            return

        badges = event.get("badges") or []
        is_moderator = any(
            str((badge or {}).get("set_id") or "").lower() == "moderator"
            for badge in badges
        )
        sender = {
            "user_id": uid,
            "username": username,
            "is_moderator": is_moderator,
            "is_broadcaster": str(uid or "") == str(event.get("broadcaster_user_id") or ""),
        }
        normalized = {
            "broadcaster": {"user_id": bid, "username": conn["username"]},
            "sender": sender,
            "content": str((event.get("message") or {}).get("text") or ""),
            "sn7_profile_id": conn["broadcaster_user_id"],
            "platform": "twitch",
        }

        # A interação no chat é o sinal de presença disponível sem consultar
        # continuamente uma API de viewers. O cache de services.py evita SQL
        # repetido durante o intervalo configurado.
        if username:
            try:
                _reward_executor.submit(
                    award_watch_presence, bid, username, uid, "twitch"
                )
            except Exception as exc:
                print(f"[SN7-REWARDS] fila presença Twitch falhou: {exc}", flush=True)

        print(
            f"[TWITCH-CHAT] {username or 'usuário'}: "
            f"{normalized['content']} -> perfil {conn['broadcaster_user_id']}",
            flush=True,
        )
        _process_chat(normalized, lambda _bid, message: _send_chat(conn, message))
    except Exception as exc:
        print(f"[TWITCH-CHAT] event processing falhou: {exc}", flush=True)

