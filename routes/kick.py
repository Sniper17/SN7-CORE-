import base64
import hashlib
import secrets
import time
from urllib.parse import urlencode, quote, urlparse
import ipaddress
import socket

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa, ed25519
from flask import Blueprint, jsonify, redirect, request, session

from core.database import get_conn
from core.services import ensure_channel, ensure_player, get_channel, get_player, get_rank, award_watch_presence, add_points, get_point_rewards
from core.command_system import find_command, list_commands
from core.auth import get_session_broadcaster_id


kick_bp = Blueprint("kick", __name__)

KICK_API = "https://api.kick.com/public/v1"
KICK_ID = "https://id.kick.com"
KICK_PUBLIC_KEY_URL = f"{KICK_API}/public-key"

_oauth_states = {}
_public_key_pem = None

# Cache curto do estado da live para não consultar a API da Kick a cada mensagem.
_live_status_cache = {}
_live_status_cache_ttl = 10
_app_access_token = None
_app_access_token_expires_at = 0
_bot_status_cache = {}
_bot_status_cache_ttl = 30


def _env(name, default=""):
    import os
    return os.environ.get(name, default).strip()


def _client_id():
    return _env("KICK_CLIENT_ID")


def _client_secret():
    return _env("KICK_CLIENT_SECRET")


def _kick_app_access_token():
    """Obtém token de aplicação para consultar dados públicos da Kick."""
    global _app_access_token, _app_access_token_expires_at
    now = int(time.time())
    if _app_access_token and _app_access_token_expires_at > now + 30:
        return _app_access_token

    client_id = _client_id()
    client_secret = _client_secret()
    if not client_id or not client_secret:
        return None

    try:
        response = requests.post(
            f"{KICK_ID}/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=10,
        )
        data = response.json()
    except Exception as exc:
        print(f"[KICK-LIVE] erro obtendo app access token: {exc}", flush=True)
        return None

    if response.status_code >= 400 or not data.get("access_token"):
        print(f"[KICK-LIVE] app token falhou HTTP {response.status_code}: {data}", flush=True)
        return None

    _app_access_token = str(data["access_token"])
    _app_access_token_expires_at = now + int(data.get("expires_in") or 3600)
    return _app_access_token


def _kick_channel_is_live(broadcaster_id):
    """Retorna True somente quando a Kick confirma que o canal está ao vivo."""
    try:
        bid = int(broadcaster_id)
    except (TypeError, ValueError):
        return False

    now = time.time()
    cached = _live_status_cache.get(bid)
    if cached and now - cached[0] < _live_status_cache_ttl:
        return bool(cached[1])

    token = _kick_app_access_token()
    if not token:
        # Falha fechada: sem confirmação da live, não concede pontos de presença.
        _live_status_cache[bid] = (now, False)
        return False

    try:
        response = requests.get(
            f"{KICK_API}/livestreams",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            params={"broadcaster_user_id": bid},
            timeout=10,
        )
        data = response.json()
        if response.status_code >= 400:
            print(f"[KICK-LIVE] HTTP {response.status_code}: {data}", flush=True)
            live = False
        else:
            live = bool(data.get("data"))
    except Exception as exc:
        print(f"[KICK-LIVE] erro consultando status: {exc}", flush=True)
        live = False

    _live_status_cache[bid] = (now, live)
    return live


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
    username = str(user.get("username") or user.get("slug") or user.get("channel_slug") or user.get("name") or user.get("display_name") or "").strip()
    profile_picture_url = str(user.get("profile_picture") or user.get("profile_picture_url") or user.get("profile_pic") or user.get("avatar_url") or "").strip()

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO kick_connections
                   (broadcaster_user_id, username, profile_picture_url, access_token, refresh_token, expires_at, scope, updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,NOW())
                   ON CONFLICT (broadcaster_user_id) DO UPDATE SET
                     username=EXCLUDED.username,
                     profile_picture_url=CASE WHEN EXCLUDED.profile_picture_url <> '' THEN EXCLUDED.profile_picture_url ELSE kick_connections.profile_picture_url END,
                     access_token=EXCLUDED.access_token,
                     refresh_token=COALESCE(EXCLUDED.refresh_token,kick_connections.refresh_token),
                     expires_at=EXCLUDED.expires_at,
                     scope=EXCLUDED.scope,
                     updated_at=NOW()""",
                (broadcaster_id, username, profile_picture_url, token_data.get("access_token"),
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
                """SELECT broadcaster_user_id, username, profile_picture_url, access_token, refresh_token, expires_at, scope
                   FROM kick_connections WHERE broadcaster_user_id=%s""",
                (int(broadcaster_id),),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return {
        "broadcaster_user_id": row[0], "username": row[1], "profile_picture_url": row[2] or "",
        "access_token": row[3], "refresh_token": row[4],
        "expires_at": row[5] or 0, "scope": row[6] or "",
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
    """Recria a assinatura de chat e confirma no próprio endpoint da Kick."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    params = {}
    if broadcaster_id is not None:
        params["broadcaster_user_id"] = int(broadcaster_id)

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

    print(f"[KICK-EVENTS] subscriptions -> HTTP {response.status_code}: {data}", flush=True)
    if response.status_code >= 400:
        raise RuntimeError(f"Falha ao consultar assinaturas Kick: HTTP {response.status_code}: {data}")

    existing = data.get("data") or []
    chat_ids = [
        str(item.get("id"))
        for item in existing
        if item.get("id") and str(item.get("event") or "") in {"chat.message.sent", "channel.subscription.new", "channel.subscription.renewal", "kicks.gifted"}
    ]

    if chat_ids:
        delete_response = requests.delete(
            f"{KICK_API}/events/subscriptions",
            headers=headers,
            params=[("id", subscription_id) for subscription_id in chat_ids],
            timeout=20,
        )
        delete_text = delete_response.text[:1000]
        print(
            f"[KICK-EVENTS] delete chat subscriptions {chat_ids} -> "
            f"HTTP {delete_response.status_code}: {delete_text}",
            flush=True,
        )
        if delete_response.status_code not in (200, 204):
            try:
                delete_data = delete_response.json()
            except Exception:
                delete_data = delete_text
            raise RuntimeError(
                f"Falha ao remover assinaturas antigas: HTTP "
                f"{delete_response.status_code}: {delete_data}"
            )

    # Com user access token a Kick ignora broadcaster_user_id e usa o canal
    # autorizado. Omitimos o campo para seguir exatamente a API atual.
    payload = {
        "events": [
            {"name": "chat.message.sent", "version": 1},
            {"name": "channel.subscription.new", "version": 1},
            {"name": "channel.subscription.renewal", "version": 1},
            {"name": "kicks.gifted", "version": 1},
        ],
        "method": "webhook",
    }
    print(
        f"[KICK-EVENTS] criando chat.message.sent para broadcaster={broadcaster_id} "
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
        create_data = response.json()
    except Exception:
        create_data = {"raw": response.text[:1000]}

    print(
        f"[KICK-EVENTS] CREATE chat.message.sent -> "
        f"HTTP {response.status_code}: {create_data}",
        flush=True,
    )

    if response.status_code >= 400:
        raise RuntimeError(
            f"Falha ao criar chat.message.sent: HTTP "
            f"{response.status_code}: {create_data}"
        )

    created_items = create_data.get("data") or []
    event_errors = [
        item for item in created_items
        if str(item.get("name") or "") == "chat.message.sent"
        and item.get("error")
    ]
    if event_errors:
        raise RuntimeError(
            f"Kick recusou chat.message.sent: {event_errors}"
        )

    # Confirma no GET que a assinatura realmente existe.
    verify_response = requests.get(
        f"{KICK_API}/events/subscriptions",
        headers=headers,
        params=params,
        timeout=20,
    )
    try:
        verify_data = verify_response.json()
    except Exception:
        verify_data = {"raw": verify_response.text[:1000]}

    if verify_response.status_code >= 400:
        raise RuntimeError(
            f"Assinatura criada, mas não foi possível confirmar: "
            f"HTTP {verify_response.status_code}: {verify_data}"
        )

    verified = [
        item for item in (verify_data.get("data") or [])
        if str(item.get("event") or "") == "chat.message.sent"
    ]
    if not verified:
        raise RuntimeError(
            "A Kick respondeu à criação, mas o chat.message.sent não aparece "
            "na lista de assinaturas."
        )

    return {
        "ok": True,
        "already": False,
        "recreated": True,
        "deleted_subscription_ids": chat_ids,
        "subscription": verified[0],
        "data": create_data,
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


def _mention(username):
    """Formata um nome de usuário para o Kick reconhecer como menção."""
    value = str(username or "").strip()
    if not value:
        return ""
    return value if value.startswith("@") else f"@{value}"



def _extract_command_values(args):
    values = {}

    normalized = [
        str(arg or "").strip()
        for arg in (args or [])
        if str(arg or "").strip()
    ]

    # Primeiro usuário mencionado explicitamente com @.
    for arg in normalized:
        if arg.startswith("@") and len(arg) > 1:
            values["target"] = _mention(arg)
            break

    # Se não houver @, aceita um argumento textual como usuário-alvo.
    if "target" not in values:
        for arg in normalized:
            if not arg.lstrip("-").isdigit():
                values["target"] = _mention(arg)
                break

    # Primeiro número encontrado = quantidade.
    for arg in normalized:
        try:
            values["amount"] = int(arg)
            break
        except (TypeError, ValueError):
            continue

    return values

def _is_public_http_url(url):
    """Valida uma URL externa antes de permitir uma consulta customapi."""
    try:
        parsed = urlparse(str(url or "").strip())
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            return False
        hostname = parsed.hostname
        try:
            ip = ipaddress.ip_address(hostname)
            return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified)
        except ValueError:
            pass

        infos = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
        addresses = {info[4][0] for info in infos}
        if not addresses:
            return False
        for address in addresses:
            try:
                ip = ipaddress.ip_address(address)
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
                    return False
            except ValueError:
                return False
        return True
    except Exception:
        return False


def _find_balanced_expression(text, start):
    """Encontra o fechamento correspondente de uma expressão $(...)."""
    depth = 0
    for index in range(start, len(text)):
        if text.startswith("$(", index):
            depth += 1
            continue
        if text[index] == ")" and depth:
            depth -= 1
            if depth == 0:
                return index
    return None


def _expand_customapi(template, args):
    """Expande apenas customapi/queryescape e o argumento $(1:), sem tocar nas demais variáveis."""
    args = [str(arg or "").strip() for arg in (args or [])]
    whole_args = " ".join(arg for arg in args if arg)
    first_arg = args[0] if args else ""

    def evaluate(text, allow_customapi=True):
        text = str(text or "")
        out = []
        cursor = 0
        while cursor < len(text):
            start = text.find("$(", cursor)
            if start < 0:
                out.append(text[cursor:])
                break

            out.append(text[cursor:start])
            end = _find_balanced_expression(text, start)
            if end is None:
                out.append(text[start:])
                break

            expression = text[start + 2:end].strip()
            lowered = expression.lower()

            if lowered == "1:":
                out.append(whole_args)
            elif lowered == "1":
                out.append(first_arg)
            elif lowered.startswith("queryescape "):
                inner = evaluate(expression[len("queryescape "):].strip(), allow_customapi=False)
                out.append(quote(inner, safe=""))
            elif lowered.startswith("customapi ") and allow_customapi:
                url = evaluate(expression[len("customapi "):].strip(), allow_customapi=False).strip()
                out.append(_call_customapi(url))
            else:
                # Mantém intactas todas as variáveis que ainda não fazem parte desta melhoria.
                out.append(text[start:end + 1])

            cursor = end + 1
        return "".join(out)

    return evaluate(template)


def _call_customapi(url):
    """Consulta uma API externa HTTPS com timeout curto e devolve texto simples."""
    url = str(url or "").strip()
    if len(url) > 2048 or not _is_public_http_url(url):
        print(f"[CUSTOMAPI] URL bloqueada: {url[:200]}", flush=True)
        return "❌ API externa inválida."

    try:
        response = requests.get(
            url,
            headers={"Accept": "text/plain, application/json;q=0.9, */*;q=0.8"},
            timeout=5,
            allow_redirects=False,
        )
        if response.status_code >= 400:
            print(f"[CUSTOMAPI] HTTP {response.status_code}: {url}", flush=True)
            return "❌ API indisponível."

        return response.text.strip()[:450]
    except requests.RequestException as exc:
        print(f"[CUSTOMAPI] erro consultando {url}: {exc}", flush=True)
        return "❌ API indisponível."
    except Exception as exc:
        print(f"[CUSTOMAPI] erro inesperado: {exc}", flush=True)
        return "❌ API indisponível."


def _render_response(template, values, args=None):
    text = str(template or "")
    for key, value in values.items():
        text = text.replace(f"$({key})", str(value))
    if values.get("rank") is None:
        text = text.replace("#None", "")
        text = text.replace("$(rank)", "")
    text = _expand_customapi(text, args or [])
    return " ".join(text.split())


def _expire_pending_bets(bid):
    """Cancela apostas pendentes que passaram dos 90 segundos sem remover pontos."""
    expired = []
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE pending_bets
                   SET status='cancelled'
                 WHERE broadcaster_user_id=%s
                   AND status='pending'
                   AND expires_at < NOW()
                RETURNING challenger, defender, amount
                """,
                (int(bid),),
            )
            expired = cur.fetchall()
        conn.commit()
    finally:
        conn.close()
    return expired


def _format_balance(bid, user):
    ch = get_channel(bid)
    p = get_player(bid, user)
    rank = get_rank(bid, user)
    emoji = str(ch["currency_emoji"] or "").strip()
    return {
        "user": _mention(user),
        "points": int(p["points"]),
        "currency": ch["currency_name"],
        "emoji": emoji,
        "emoji_text": f" {emoji}" if emoji else "",
        "rank": rank if rank is not None else "",
        "rank_text": f" Sua posição no ranking é #{rank}." if rank is not None else "",
    }
def _format_ranking(bid):
 ch=get_channel(bid);limit=max(1,min(int(ch['rank_limit']),10));c=get_conn()
 try:
  with c.cursor() as x:x.execute('SELECT username,points FROM players WHERE broadcaster_user_id=%s AND points>0 ORDER BY points DESC,username ASC LIMIT %s',(int(bid),limit));rows=x.fetchall()
 finally:c.close()
 if not rows:return f"🏆 Ranking: ninguém possui {ch['currency_name']} ainda."
 return '🏆 Ranking: '+' • '.join(f'{i}. {n} {p}' for i,(n,p) in enumerate(rows,1))
def _commands_text(bid):
 a=[x for x in list_commands(bid) if x['category']=='custom' and x['enabled']];return '📜 Nenhum comando personalizado configurado.' if not a else '📜 Comandos: '+' '.join(x['command'] for x in a[:25])
def _is_moderator(sender, broadcaster_id):
    if str(sender.get("user_id") or "") == str(broadcaster_id):
        return True
    if sender.get("is_moderator") or sender.get("is_broadcaster") or sender.get("is_owner"):
        return True
    identity = sender.get("identity") or {}
    for badge in identity.get("badges") or []:
        badge_type = str((badge or {}).get("type") or "").lower()
        if badge_type in {"moderator", "broadcaster", "owner"}:
            return True
    return False


def _process_chat(payload):
    broadcaster = payload.get("broadcaster") or {}
    sender = payload.get("sender") or {}
    try:
        bid = int(broadcaster.get("user_id"))
        uid = int(sender.get("user_id")) if sender.get("user_id") else None
    except (TypeError, ValueError):
        print(f"[KICK-CHAT] payload sem IDs válidos: {payload}", flush=True)
        return

    user = str(sender.get("username") or sender.get("slug") or "").strip()
    content = str(payload.get("content") or "").strip()
    if not bid or not user:
        return

    # Expira apostas antigas na primeira interação seguinte. Nenhum ponto é removido.
    try:
        expired_bets = _expire_pending_bets(bid)
        for challenger, defender, amount in expired_bets:
            _send_chat(
                bid,
                f"⏰ A aposta entre {_mention(challenger)} e {_mention(defender)} expirou após 90 segundos. Nenhum ponto foi removido.",
            )
    except Exception as exc:
        print(f"[KICK-BET] erro expirando apostas: {exc}", flush=True)

    # A presença individual é inferida por mensagens no chat.
    # Só concede o bônus se a Kick confirmar que a live está ao vivo.
    if _kick_channel_is_live(bid):
        try:
            presence_bonus = award_watch_presence(bid, user, uid)
            if presence_bonus:
                print(f"[SN7-REWARDS] {user} +{presence_bonus} por presença em live", flush=True)
        except Exception as exc:
            print(f"[SN7-REWARDS] erro presença: {exc}", flush=True)

    if not content.startswith("!"):
        return

    ensure_channel(
        bid,
        str(broadcaster.get("username") or broadcaster.get("channel_slug") or ""),
    )
    ensure_player(bid, user, uid)

    pieces = content.split()
    cmd = pieces[0].lower()
    args = pieces[1:]

    try:
        cfg = find_command(bid, cmd)
        print(
            f"[KICK-CHAT] {user}: {content} -> "
            f"{cfg['command_key'] if cfg else 'não encontrado'}",
            flush=True,
        )
        if not cfg or not cfg["enabled"]:
            return

        ch = get_channel(bid)
        key = cfg["command_key"]

        if key == "wzclass":
            query = " ".join(args).strip()

            try:
                from core.warzone.service import resolve_wzclass
                wzclass = resolve_wzclass(query)
            except Exception as exc:
                print(f"[WZCLASS] erro interno: {exc}", flush=True)
                wzclass = "❌ Não consegui consultar a classe Warzone agora."

            response = _render_response(
                cfg["response"],
                {
                    "wzclass": wzclass,
                    "weapon": query,
                    "query": query,
                },
                args=args,
            )

            _send_chat(bid, response)
            return

        currency = str(ch["currency_name"])
        emoji = str(ch["currency_emoji"])

        if key == "points":
            _send_chat(bid, _render_response(cfg["response"], _format_balance(bid, user)))
            return

        if key == "ranking":
            _send_chat(bid, _render_response(cfg["response"], {"ranking": _format_ranking(bid)}))
            return

        if key == "cmds":
            _send_chat(bid, _render_response(cfg["response"], {"commands": _commands_text(bid)}))
            return

        if key == "duel":
            # !aposta @usuario quantidade cria uma aposta pendente.
            command_values = _extract_command_values(args)
            target_value = str(command_values.get("target") or "").strip().lstrip("@")
            amount_value = command_values.get("amount")

            if not target_value or amount_value is None or int(amount_value) <= 0:
                _send_chat(
                    bid,
                    _render_response(
                        cfg["response"],
                        {
                            "user": _mention(user),
                            "target": _mention(target_value),
                            "amount": amount_value if amount_value is not None else "",
                            "command": cfg["command"],
                            "duel_result": f"⚔️ Use {cfg['command']} @usuário quantidade",
                            "currency": currency,
                            "emoji": emoji,
                        },
                    ),
                )
                return

            amount_value = int(amount_value)
            if target_value.lower() == user.lower():
                _send_chat(
                    bid,
                    _render_response(
                        cfg["response"],
                        {
                            "user": _mention(user),
                            "target": _mention(target_value),
                            "amount": amount_value,
                            "command": cfg["command"],
                            "duel_result": "⚔️ Você não pode apostar consigo mesmo.",
                            "currency": currency,
                            "emoji": emoji,
                        },
                    ),
                )
                return

            ensure_player(bid, target_value)

            conn = get_conn()
            try:
                with conn.cursor() as cur:
                    # Remove apostas expiradas do mesmo canal.
                    cur.execute(
                        "DELETE FROM pending_bets WHERE broadcaster_user_id=%s AND (status<>'pending' OR expires_at<NOW())",
                        (bid,),
                    )

                    cur.execute(
                        """
                        SELECT id FROM pending_bets
                         WHERE broadcaster_user_id=%s
                           AND status='pending'
                           AND (challenger=%s OR defender=%s OR challenger=%s OR defender=%s)
                         LIMIT 1
                        """,
                        (bid, user, user, target_value, target_value),
                    )
                    existing = cur.fetchone()
                    if existing:
                        _send_chat(
                            bid,
                            f"⚔️ Já existe uma aposta pendente envolvendo {_mention(user)} ou {_mention(target_value)}."
                        )
                        return

                    cur.execute(
                        """
                        SELECT points FROM players
                         WHERE broadcaster_user_id=%s AND username=%s
                        FOR UPDATE
                        """,
                        (bid, user),
                    )
                    row = cur.fetchone()
                    current_points = int(row[0] or 0) if row else 0

                    if current_points < amount_value:
                        _send_chat(
                            bid,
                            f"❌ {_mention(user)} não tem {amount_value} {currency} para apostar. Saldo: {current_points}."
                        )
                        return

                    cur.execute(
                        """
                        INSERT INTO pending_bets
                            (broadcaster_user_id, challenger, defender, amount)
                        VALUES (%s,%s,%s,%s)
                        RETURNING id
                        """,
                        (bid, user, target_value, amount_value),
                    )
                    bet_id = cur.fetchone()[0]
                conn.commit()
            finally:
                conn.close()

            accept_cfg = find_command(bid, "!aceitar")
            decline_cfg = find_command(bid, "!recusar")
            accept_command = accept_cfg["command"] if accept_cfg else "!aceitar"
            decline_command = decline_cfg["command"] if decline_cfg else "!recusar"

            duel_values = {
                "user": _mention(user),
                "target": _mention(target_value),
                "amount": amount_value,
                "command": cfg["command"],
                "accept_command": accept_command,
                "decline_command": decline_command,
                "duel_result": (
                    f"⚔️ {_mention(user)} está apostando {amount_value} {currency} contra {_mention(target_value)}. "
                    f"Digite {accept_command} ou {decline_command}."
                ),
                "attacker": _mention(user),
                "defender": _mention(target_value),
                "winner": "",
                "loser": "",
                "win": amount_value,
                "loss": amount_value,
                "currency": currency,
                "emoji": emoji,
                "bet_id": bet_id,
            }
            _send_chat(bid, _render_response(cfg["response"], duel_values))
            return

        if key in {"bet_accept", "bet_decline"}:
            conn = get_conn()
            first_message = None
            result_message = None
            response_template = cfg.get("response") or "$(bet_result)"
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT id, challenger, defender, amount
                          FROM pending_bets
                         WHERE broadcaster_user_id=%s
                           AND defender=%s
                           AND status='pending'
                           AND expires_at>=NOW()
                         ORDER BY created_at DESC
                         LIMIT 1
                         FOR UPDATE
                        """,
                        (bid, user),
                    )
                    bet = cur.fetchone()

                    if not bet:
                        result_message = "⚔️ Você não possui uma aposta pendente para aceitar ou recusar."
                    elif key == "bet_decline":
                        cur.execute(
                            "UPDATE pending_bets SET status='declined' WHERE id=%s",
                            (bet[0],),
                        )
                        result_message = (
                            f"❌ {_mention(user)} recusou a aposta de {_mention(bet[1])}. Nenhum ponto foi removido."
                        )
                    else:
                        bet_id, challenger, defender, amount = bet
                        amount = int(amount)

                        cur.execute(
                            """
                            SELECT username, points
                              FROM players
                             WHERE broadcaster_user_id=%s
                               AND username IN (%s,%s)
                             FOR UPDATE
                            """,
                            (bid, challenger, defender),
                        )
                        balances = {row[0]: int(row[1] or 0) for row in cur.fetchall()}
                        challenger_points = balances.get(challenger, 0)
                        defender_points = balances.get(defender, 0)

                        if challenger_points < amount:
                            cur.execute(
                                "UPDATE pending_bets SET status='cancelled' WHERE id=%s",
                                (bet_id,),
                            )
                            result_message = (
                                f"❌ A aposta foi cancelada: {_mention(challenger)} não possui mais "
                                f"{amount} {currency}. Nenhum ponto foi removido."
                            )
                        elif defender_points < amount:
                            cur.execute(
                                "UPDATE pending_bets SET status='cancelled' WHERE id=%s",
                                (bet_id,),
                            )
                            result_message = (
                                f"❌ {_mention(user)} não possui {amount} {currency} para aceitar a aposta. "
                                "Nenhum ponto foi removido."
                            )
                        else:
                            import random
                            winner = challenger if random.choice([True, False]) else defender
                            loser = defender if winner == challenger else challenger

                            cur.execute(
                                """
                                UPDATE players
                                   SET points=points+%s, duels=duels+1, updated_at=NOW()
                                 WHERE broadcaster_user_id=%s AND username=%s
                                """,
                                (amount, bid, winner),
                            )
                            cur.execute(
                                """
                                UPDATE players
                                   SET points=GREATEST(0,points-%s), duels=duels+1, updated_at=NOW()
                                 WHERE broadcaster_user_id=%s AND username=%s
                                """,
                                (amount, bid, loser),
                            )
                            cur.execute(
                                "UPDATE pending_bets SET status='accepted' WHERE id=%s",
                                (bet_id,),
                            )
                            cur.execute(
                                """
                                INSERT INTO duel_events
                                    (broadcaster_user_id,attacker,defender,winner,
                                     winner_points_delta,loser_points_delta)
                                VALUES (%s,%s,%s,%s,%s,%s)
                                """,
                                (bid, challenger, defender, winner, amount, -amount),
                            )
                            first_message = (
                                f"✅ {_mention(user)} aceitou a aposta de {_mention(challenger)}. 🎲 Rolando dados..."
                            )
                            result_message = (
                                f"🏆 {_mention(winner)} foi o vencedor da aposta e levou {amount} {currency}."
                            )

                conn.commit()
            finally:
                conn.close()

            if first_message:
                _send_chat(
                    bid,
                    _render_response(
                        response_template,
                        {
                            "user": _mention(user),
                            "target": _mention(challenger),
                            "amount": amount,
                            "currency": currency,
                            "bet_result": first_message,
                        },
                    ),
                )
                _send_chat(bid, result_message)
            else:
                _send_chat(
                    bid,
                    _render_response(
                        response_template,
                        {
                            "user": _mention(user),
                            "target": _mention(bet[1]) if bet else "",
                            "amount": int(bet[3]) if bet else "",
                            "currency": currency,
                            "bet_result": result_message or "⚔️ A aposta não pôde ser processada.",
                        },
                    ),
                )
            return

        ismod = _is_moderator(sender, bid)
        if key in {"addcmd", "addpoint", "settpoint", "delcmd"} and not ismod:
            _send_chat(bid, "⛔ Apenas streamer/mod pode usar este comando.")
            return

        if key == "addcmd":
            if len(args) < 2:
                _send_chat(bid, "Use !addcmd !comando resposta")
                return

            custom = args[0].lower()
            custom = custom if custom.startswith("!") else "!" + custom
            resp = " ".join(args[1:]).strip()
            if len(custom) > 64:
                _send_chat(bid, "❌ Comando muito longo.")
                return
            if not resp:
                _send_chat(bid, "❌ Informe uma resposta.")
                return

            all_commands = list_commands(bid)
            used = {x["command"] for x in all_commands}
            used.update(alias for x in all_commands for alias in x["aliases"])
            if custom in used:
                _send_chat(bid, "⛔ Essa palavra de ativação já está em uso.")
                return

            conn = get_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO command_configs
                            (broadcaster_user_id,command_key,command,description,response,enabled,category,is_system)
                        VALUES (%s,%s,%s,%s,%s,TRUE,'custom',FALSE)
                        """,
                        (bid, "custom:" + custom, custom,
                         "Comando personalizado desta live.", resp[:500]),
                    )
                conn.commit()
            finally:
                conn.close()

            _send_chat(
                bid,
                _render_response(cfg["response"], {"command": custom}),
            )
            return

        if key in {"addpoint", "settpoint"}:
            if len(args) < 2:
                _send_chat(bid, f'Use {cfg["command"]} @usuário quantidade')
                return

            target = args[0].lstrip("@").strip()
            try:
                amount = int(args[1])
                amount = max(0, amount) if key == "settpoint" else amount
            except ValueError:
                _send_chat(bid, "❌ Quantidade inválida.")
                return

            if key == "addpoint" and amount <= 0:
                _send_chat(bid, "❌ A quantidade precisa ser maior que 0.")
                return

            ensure_player(bid, target)
            conn = get_conn()
            try:
                with conn.cursor() as cur:
                    expression = "points+%s" if key == "addpoint" else "%s"
                    cur.execute(
                        f"""
                        UPDATE players
                           SET points={expression}, updated_at=NOW()
                         WHERE broadcaster_user_id=%s AND username=%s
                         RETURNING points
                        """,
                        (amount, bid, target),
                    )
                    row = cur.fetchone()
                conn.commit()
            finally:
                conn.close()

            new_points = int(row[0]) if row else amount
            _send_chat(
                bid,
                _render_response(
                    cfg["response"],
                    {
                        "target": _mention(target),
                        "amount": amount,
                        "new_points": new_points,
                        "currency": currency,
                        "emoji": emoji,
                    },
                ),
            )
            return

        if key == "delcmd":
            if not args:
                _send_chat(bid, "Use !delcmd !comando")
                return

            target = find_command(bid, args[0].lower())
            if not target or target["is_system"]:
                _send_chat(bid, "❌ Esse comando personalizado não existe.")
                return

            conn = get_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM command_configs WHERE broadcaster_user_id=%s AND command_key=%s AND is_system=FALSE",
                        (bid, target["command_key"]),
                    )
                    deleted = cur.rowcount > 0
                conn.commit()
            finally:
                conn.close()

            _send_chat(
                bid,
                _render_response(cfg["response"], {"command": args[0]})
                if deleted else "❌ Comando não existe.",
            )
            return

        if not cfg["is_system"]:
            custom_values = {
                "user": _mention(user),
            }

            # Extrai automaticamente usuário-alvo e quantidade.
            custom_values.update(_extract_command_values(args))

            _send_chat(
                bid,
                _render_response(cfg["response"], custom_values, args=args)
            )

    except Exception as exc:
        print(f"[KICK-CHAT] erro processando {content!r}: {exc}", flush=True)

def _process_webhook(payload, event_type):
    print(f"[KICK-WEBHOOK] evento recebido para processamento: {event_type}", flush=True)
    if event_type == "chat.message.sent":
        print("[KICK-WEBHOOK] processando chat.message.sent", flush=True)
        _process_chat(payload)
        return

    broadcaster = payload.get("broadcaster") or {}
    try:
        bid = int(broadcaster.get("user_id"))
    except (TypeError, ValueError):
        return
    rewards = get_point_rewards(bid)

    if event_type in {"channel.subscription.new", "channel.subscription.renewal"}:
        subscriber = payload.get("subscriber") or {}
        username = str(subscriber.get("username") or subscriber.get("slug") or "").strip()
        uid = subscriber.get("user_id")
        if username and rewards["sub_bonus"] > 0:
            add_points(bid, username, rewards["sub_bonus"], uid)
            print(f"[SN7-REWARDS] {username} +{rewards['sub_bonus']} por assinatura ({event_type})", flush=True)
        return

    if event_type == "kicks.gifted":
        sender = payload.get("sender") or {}
        gift = payload.get("gift") or {}
        username = str(sender.get("username") or sender.get("slug") or "").strip()
        uid = sender.get("user_id")
        try: amount = max(0, int(gift.get("amount") or 0))
        except (TypeError, ValueError): amount = 0
        bonus = amount * rewards["kicks_bonus_per_kick"]
        if username and bonus > 0:
            add_points(bid, username, bonus, uid)
            print(f"[SN7-REWARDS] {username} +{bonus} por {amount} KICK(s)", flush=True)


def _session_broadcaster_id():
    try:
        value = get_session_broadcaster_id()
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _chat_subscription_ids(access_token):
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    response = requests.get(
        f"{KICK_API}/events/subscriptions",
        headers=headers,
        timeout=8,
    )
    try:
        data = response.json()
    except Exception:
        data = {}
    if response.status_code >= 400:
        raise RuntimeError(f"Falha ao consultar assinaturas Kick: HTTP {response.status_code}")
    return [
        str(item.get("id"))
        for item in (data.get("data") or [])
        if item.get("id") and str(item.get("event") or "") in {"chat.message.sent", "channel.subscription.new", "channel.subscription.renewal", "kicks.gifted"}
    ]


def _unsubscribe_chat(access_token):
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    ids = _chat_subscription_ids(access_token)
    if not ids:
        return {"ok": True, "active": False, "deleted_subscription_ids": []}
    response = requests.delete(
        f"{KICK_API}/events/subscriptions",
        headers=headers,
        params=[("id", x) for x in ids],
        timeout=20,
    )
    if response.status_code not in (200, 204):
        raise RuntimeError(f"Falha ao desativar o bot: HTTP {response.status_code}")
    return {"ok": True, "active": False, "deleted_subscription_ids": ids}


def _bot_active(access_token, broadcaster_id=None):
    try:
        bid = int(broadcaster_id) if broadcaster_id is not None else None
    except (TypeError, ValueError):
        bid = None
    now = time.time()
    if bid is not None:
        cached = _bot_status_cache.get(bid)
        if cached and now - cached[0] < _bot_status_cache_ttl:
            return bool(cached[1])
    active = bool(_chat_subscription_ids(access_token))
    if bid is not None:
        _bot_status_cache[bid] = (now, active)
    return active



def _save_profile_picture(broadcaster_id, url):
    url = str(url or "").strip()
    if not url:
        return
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE kick_connections SET profile_picture_url=%s, updated_at=NOW() WHERE broadcaster_user_id=%s", (url, int(broadcaster_id)))
        conn.commit()
    finally:
        conn.close()

def _save_username(broadcaster_id, username):
    username=str(username or "").strip()
    if not username:
        return
    conn=get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE kick_connections SET username=%s, updated_at=NOW() WHERE broadcaster_user_id=%s",(username,int(broadcaster_id)))
        conn.commit()
    finally:
        conn.close()


@kick_bp.get("/me")
def me():
    bid = _session_broadcaster_id()
    if bid is None:
        return jsonify({"ok": True, "authenticated": False, "user": None, "bot": {"active": False}})
    conn = _valid_connection(bid)
    if not conn:
        return jsonify({"ok": True, "authenticated": False, "user": None, "bot": {"active": False}})
    username=str(conn.get("username") or "").strip()
    profile_picture_url=str(conn.get("profile_picture_url") or "").strip()
    if not username or not profile_picture_url:
        try:
            ku=_kick_user(conn["access_token"])
            username=str(ku.get("username") or ku.get("slug") or ku.get("channel_slug") or ku.get("name") or ku.get("display_name") or username).strip()
            profile_picture_url=str(ku.get("profile_picture") or ku.get("profile_picture_url") or ku.get("profile_pic") or ku.get("avatar_url") or profile_picture_url).strip()
            if username: _save_username(bid,username)
            if profile_picture_url: _save_profile_picture(bid,profile_picture_url)
        except Exception as exc:
            print(f"[KICK-BOT] não foi possível atualizar perfil: {exc}",flush=True)

    try:
        active=_bot_active(conn["access_token"], bid)
    except Exception as exc:
        print(f"[KICK-BOT] status falhou: {exc}",flush=True)
        active=False
    return jsonify({
        "ok": True,
        "authenticated": True,
        "user": {"id": int(bid), "username": username, "profile_picture_url": profile_picture_url},
        "bot": {"active": active},
    })


@kick_bp.post("/bot/toggle")
def bot_toggle():
    bid = _session_broadcaster_id()
    if bid is None:
        return jsonify({"ok": False, "error": "Faça login com a Kick primeiro."}), 401
    conn = _valid_connection(bid)
    if not conn:
        return jsonify({"ok": False, "error": "Conta Kick não conectada ao SN7 Core."}), 403
    payload = request.get_json(silent=True) or {}
    desired = payload.get("active")
    if not isinstance(desired, bool):
        return jsonify({"ok": False, "error": "active precisa ser true ou false."}), 400
    try:
        if desired:
            result = _subscribe_chat(conn["access_token"], bid)
        else:
            result = _unsubscribe_chat(conn["access_token"])
        _bot_status_cache[int(bid)] = (time.time(), bool(desired))
        return jsonify({"ok": True, "active": desired, "result": result})
    except Exception as exc:
        print(f"[KICK-BOT] toggle falhou broadcaster={bid}: {exc}", flush=True)
        return jsonify({"ok": False, "error": "Não foi possível alterar o status do bot."}), 502


@kick_bp.post("/logout")
def logout():
    session.pop("kick_broadcaster_id", None)
    session.pop("kick_oauth_state", None)
    session.pop("kick_code_verifier", None)
    session.pop("kick_oauth_next", None)
    return jsonify({"ok": True})


@kick_bp.get("/login")
def login():
    if not _client_id() or not _client_secret():
        return jsonify({"ok": False, "error": "KICK_CLIENT_ID/KICK_CLIENT_SECRET não configurados no Render."}), 503

    # Aceitamos somente um destino interno conhecido. Nunca redirecionamos
    # para uma URL fornecida livremente pelo navegador.
    next_page = request.args.get("next", "profile")
    if next_page != "profile":
        next_page = "profile"

    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(32)
    session["kick_oauth_state"] = state
    session["kick_code_verifier"] = verifier
    session["kick_oauth_next"] = next_page

    params = {
        "response_type": "code",
        "client_id": _client_id(),
        "redirect_uri": _redirect_uri(),
        "scope": _scopes(),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        # Ajuda a evitar a interceptação do OAuth pelo app Kick em mobile.
        "browser": "true",
    }
    return redirect(f"{KICK_ID}/oauth/authorize?{urlencode(params)}")


@kick_bp.route("/callback", methods=["GET", "POST"])
def callback():
    params = request.args if request.method == "GET" else request.form
    error = params.get("error")
    if error:
        return jsonify({"ok": False, "error": error, "description": params.get("error_description", "")}), 400
    state = params.get("state", "")
    expected = session.pop("kick_oauth_state", "")
    verifier = session.pop("kick_code_verifier", "")
    if not state or state != expected or not verifier:
        return jsonify({"ok": False, "error": "OAuth state inválido ou expirado."}), 400
    try:
        token_data = _exchange_code(params.get("code", ""), verifier)
        user = _kick_user(token_data["access_token"])
        broadcaster_id = _save_connection(user, token_data)

        # Login e ativação do bot são ações separadas.
        session["kick_broadcaster_id"] = broadcaster_id
        session.permanent = True
        session.pop("kick_oauth_next", None)

        return redirect("/dashboard?profile=1&connected=1")
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
