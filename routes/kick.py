import base64
import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import urlencode, quote, urlparse
import ipaddress
import socket
import re
import random

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa, ed25519
from flask import Blueprint, Response, jsonify, redirect, request, session

from core.database import get_conn, init_db
from core.services import ensure_channel, ensure_player, get_channel, get_player, get_rank, award_watch_presence, add_points, get_point_rewards
from core.command_system import find_command, list_commands
from core.auth import get_session_broadcaster_id, stable_channel_id
from core.cache import forget_rankings
from threading import RLock, Timer
from concurrent.futures import ThreadPoolExecutor


kick_bp = Blueprint("kick", __name__)

# Processamento serial por broadcaster: preserva a ordem das mensagens
# de cada canal sem bloquear canais diferentes.
_chat_executors = {}
_chat_executors_lock = RLock()

# Timers narrativos dos Mini Games. A sessão continua persistida no PostgreSQL;
# estes timers apenas publicam os capítulos no chat enquanto o processo está ativo.
_RACE_STORY_TIMERS = {}
_SURVIVAL_STORY_TIMERS = {}
_RACE_JOIN_TIMERS = {}
_SURVIVAL_JOIN_TIMERS = {}
_STORY_TIMER_LOCK = RLock()
STORY_CHAPTER_DELAY_SECONDS = 4
JOIN_WINDOW_SECONDS = 90

def _cancel_story_timer(store, bid, platform):
    key = (int(bid), str(platform or "kick").lower())
    with _STORY_TIMER_LOCK:
        timer = store.pop(key, None)
    if timer:
        try:
            timer.cancel()
        except Exception:
            pass

def _schedule_race_story(bid, platform, delay=STORY_CHAPTER_DELAY_SECONDS):
    key = (int(bid), str(platform or "kick").lower())
    _cancel_story_timer(_RACE_STORY_TIMERS, bid, platform)
    def run():
        try:
            from core.minigames import race_tick, race_finish
            result = race_tick(bid, platform)
            if not result.get("ok"):
                return
            if result.get("event"):
                _send_chat(bid, result["event"])
            if result.get("done"):
                final = race_finish(bid, platform)
                if final.get("ok"):
                    winners = final.get("winners") or []
                    eliminated = final.get("eliminated") or []
                    if winners:
                        _send_chat(bid, "🏁 RESULTADO DA CORRIDA! " + " • ".join(
                            f"{i+1}º {_mention(u)} +{prize} pontos" for i, (u, prize) in enumerate(winners)
                        ))
                    else:
                        _send_chat(bid, "🏁 FIM DA CORRIDA! Ninguém chegou ao final.")
                    if eliminated:
                        _send_chat(bid, "💥 Fora da corrida: " + " • ".join(_mention(u) for u in eliminated))
                return
            _schedule_race_story(bid, platform, STORY_CHAPTER_DELAY_SECONDS)
        except Exception as exc:
            print(f"[RACE-STORY] erro: {exc}", flush=True)
    timer = Timer(max(1, delay), run)
    timer.daemon = True
    with _STORY_TIMER_LOCK:
        _RACE_STORY_TIMERS[key] = timer
    timer.start()

def _schedule_survival_story(bid, platform, delay=STORY_CHAPTER_DELAY_SECONDS):
    key = (int(bid), str(platform or "kick").lower())
    _cancel_story_timer(_SURVIVAL_STORY_TIMERS, bid, platform)
    def run():
        try:
            from core.minigames import survival_tick, survival_finish
            result = survival_tick(bid, platform)
            if not result.get("ok"):
                return
            if result.get("event"):
                _send_chat(bid, result["event"])
            if result.get("done"):
                final = survival_finish(bid, platform)
                if final.get("ok"):
                    winners = final.get("winners") or []
                    dead = final.get("dead") or []
                    if winners:
                        _send_chat(bid, "🧟 RESULTADO DA SOBREVIVÊNCIA! Sobreviveram: " + " • ".join(
                            f"{_mention(u)} +{prize} pontos" for u, prize in winners
                        ))
                    else:
                        _send_chat(bid, "🧟 FIM DA SOBREVIVÊNCIA! Ninguém sobreviveu.")
                    if dead:
                        _send_chat(bid, "💀 Eliminados: " + " • ".join(_mention(u) for u in dead))
                return
            _schedule_survival_story(bid, platform, STORY_CHAPTER_DELAY_SECONDS)
        except Exception as exc:
            print(f"[SURVIVAL-STORY] erro: {exc}", flush=True)
    timer = Timer(max(1, delay), run)
    timer.daemon = True
    with _STORY_TIMER_LOCK:
        _SURVIVAL_STORY_TIMERS[key] = timer
    timer.start()

def _schedule_join_timer(store, bid, platform, callback, label):
    key = (int(bid), str(platform or "kick").lower())
    _cancel_story_timer(store, bid, platform)
    def run():
        try:
            with _STORY_TIMER_LOCK:
                store.pop(key, None)
            callback(bid, platform)
        except Exception as exc:
            print(f"[{label}] erro: {exc}", flush=True)
    timer = Timer(JOIN_WINDOW_SECONDS, run)
    timer.daemon = True
    with _STORY_TIMER_LOCK:
        store[key] = timer
    timer.start()

def _start_race_after_join_window(bid, platform):
    from core.minigames import race_begin
    begun = race_begin(bid, platform)
    if begun.get("ok") and not begun.get("already_started"):
        state = begun.get("state") or {}
        count = len(state.get("players") or [])
        _send_chat(bid, f"🏁🏎️ Inscrições encerradas! {count} corredores. A corrida começou! 3 capítulos e o resultado final!")
        _schedule_race_story(bid, platform, STORY_CHAPTER_DELAY_SECONDS)
    elif begun.get("empty"):
        _send_chat(bid, "🏁 Corrida encerrada: ninguém entrou nos 90 segundos.")

def _start_survival_after_join_window(bid, platform):
    from core.minigames import survival_begin
    begun = survival_begin(bid, platform)
    if begun.get("ok") and not begun.get("already_started"):
        state = begun.get("state") or {}
        count = len(state.get("players") or [])
        _send_chat(bid, f"🧟 Tempo encerrado! {count} participantes. A história começou! Serão 3 capítulos e o resultado final!")
        _schedule_survival_story(bid, platform, STORY_CHAPTER_DELAY_SECONDS)
    elif begun.get("empty"):
        _send_chat(bid, "🧟 Sobrevivência encerrada: ninguém entrou nos 90 segundos.")


def _chat_executor(bid):
    bid = int(bid)
    with _chat_executors_lock:
        executor = _chat_executors.get(bid)
        if executor is None:
            executor = ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix=f"sn7-kick-chat-{bid}",
            )
            _chat_executors[bid] = executor
        return executor

KICK_API = "https://api.kick.com/public/v1"
KICK_ID = "https://id.kick.com"
KICK_PUBLIC_KEY_URL = f"{KICK_API}/public-key"

_public_key_pem = None

# Cache curto do estado da live para não consultar a API da Kick a cada mensagem.
_live_status_cache = {}
_live_status_cache_ttl = 10
_app_access_token = None
_app_access_token_expires_at = 0
_bot_status_cache = {}
_bot_status_cache_ttl = 30
_profile_cache = {}
_profile_cache_ttl = 8

# Cache curto da conexão usada para enviar respostas. O caminho anterior
# consultava o PostgreSQL em praticamente toda mensagem enviada.
_connection_cache = {}
_connection_cache_ttl = 15
_connection_cache_lock = RLock()

# Cobre duplicações entregues por mais de uma assinatura/webhook durante
# reconexões. O message_id continua sendo deduplicado no PostgreSQL.
_recent_chat_events = {}
_recent_chat_events_ttl = 2.0
_recent_chat_events_lock = RLock()


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


def _pkce_challenge(verifier):
    return base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")


def _oauth_signing_secret():
    # O state do OAuth da Kick não depende mais do cookie da sessão. Isso é
    # importante quando a sessão já contém dados de outra plataforma e o
    # navegador/Render precisa trocar o cookie durante o redirecionamento.
    # Preferimos o segredo global da aplicação e usamos o client secret como
    # fallback para instalações antigas que ainda não configuraram
    # FLASK_SECRET_KEY.
    secret = _env("FLASK_SECRET_KEY") or _client_secret()
    return secret.encode("utf-8") if secret else b"sn7-kick-oauth"


def _make_kick_oauth_state(broadcaster_id):
    payload = {
        "v": 1,
        "iat": int(time.time()),
        "nonce": secrets.token_urlsafe(18),
        "broadcaster_id": int(broadcaster_id) if broadcaster_id is not None else None,
        "next": "profile",
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    signature = hmac.new(_oauth_signing_secret(), body.encode("ascii"), hashlib.sha256).digest()
    sig = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    return f"{body}.{sig}"


def _read_kick_oauth_state(state):
    try:
        body, sig = str(state or "").split(".", 1)
        expected = hmac.new(_oauth_signing_secret(), body.encode("ascii"), hashlib.sha256).digest()
        supplied = base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4))
        if not hmac.compare_digest(supplied, expected):
            return None
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        payload = json.loads(raw.decode("utf-8"))
        if payload.get("v") != 1:
            return None
        if int(time.time()) - int(payload.get("iat") or 0) > 600:
            return None
        if not payload.get("nonce"):
            return None
        return payload
    except Exception:
        return None


def _kick_pkce_verifier(state):
    # O verifier é derivado do state assinado e nunca precisa ser salvo no
    # cookie da sessão. Assim o callback continua funcionando mesmo se o
    # navegador trocar/encolher o cookie durante o OAuth.
    digest = hmac.new(
        _oauth_signing_secret(),
        ("kick-pkce:" + str(state)).encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _save_connection(user, token_data, profile_id=None):
    """Vincula a conta Kick ao perfil SN7 atual.

    broadcaster_user_id permanece como o ID real da Kick para chamadas à API;
    sn7_profile_id é o ID canônico do perfil SN7.
    """
    kick_id = int(user.get("user_id"))
    profile_id = int(profile_id or kick_id)
    expires_in = int(token_data.get("expires_in") or 0)
    expires_at = int(time.time()) + expires_in if expires_in else 0
    scope = str(token_data.get("scope") or "")
    username = str(user.get("username") or user.get("slug") or user.get("channel_slug") or user.get("name") or user.get("display_name") or "").strip()
    profile_picture_url = str(user.get("profile_picture") or user.get("profile_picture_url") or user.get("profile_pic") or user.get("avatar_url") or "").strip()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM kick_connections WHERE sn7_profile_id=%s AND broadcaster_user_id<>%s",
                (profile_id, kick_id),
            )
            cur.execute(
                """INSERT INTO kick_connections
                   (broadcaster_user_id,sn7_profile_id,username,profile_picture_url,access_token,refresh_token,expires_at,scope,updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                   ON CONFLICT (broadcaster_user_id) DO UPDATE SET
                     sn7_profile_id=EXCLUDED.sn7_profile_id,
                     username=EXCLUDED.username,
                     profile_picture_url=CASE WHEN EXCLUDED.profile_picture_url<>'' THEN EXCLUDED.profile_picture_url ELSE kick_connections.profile_picture_url END,
                     access_token=EXCLUDED.access_token,
                     refresh_token=COALESCE(EXCLUDED.refresh_token,kick_connections.refresh_token),
                     expires_at=EXCLUDED.expires_at,
                     scope=EXCLUDED.scope,
                     updated_at=NOW()""",
                (kick_id,profile_id,username,profile_picture_url,token_data.get("access_token"),
                 token_data.get("refresh_token"),expires_at,scope),
            )
        conn.commit()
    finally:
        conn.close()
    ensure_channel(profile_id, username)
    return profile_id

def _get_connection_from_db(broadcaster_id):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT broadcaster_user_id,sn7_profile_id,username,profile_picture_url,
                          access_token,refresh_token,expires_at,scope,bot_active
                     FROM kick_connections
                    WHERE broadcaster_user_id=%s OR sn7_profile_id=%s
                    ORDER BY CASE WHEN sn7_profile_id=%s THEN 0 ELSE 1 END
                    LIMIT 1""",
                (int(broadcaster_id), int(broadcaster_id), int(broadcaster_id)),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return {
        "broadcaster_user_id": int(row[0]),
        "sn7_profile_id": int(row[1] or row[0]),
        "username": row[2] or "",
        "profile_picture_url": row[3] or "",
        "access_token": row[4],
        "refresh_token": row[5],
        "expires_at": row[6] or 0,
        "scope": row[7] or "",
        "bot_active": bool(row[8]),
    }


def _get_connection(broadcaster_id):
    """Lê a conexão do cache por poucos segundos antes de consultar o banco."""
    bid = int(broadcaster_id)
    now = time.monotonic()
    with _connection_cache_lock:
        item = _connection_cache.get(bid)
        if item and item[0] > now:
            return dict(item[1]) if item[1] else None

    value = _get_connection_from_db(bid)
    with _connection_cache_lock:
        _connection_cache[bid] = (
            now + _connection_cache_ttl,
            dict(value) if value else None,
        )
    return value

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
    with _connection_cache_lock:
        _connection_cache.pop(int(broadcaster_id), None)


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
    if int(conn_data.get("expires_at") or 0) <= int(time.time()) + 10:
        return _refresh_connection(conn_data)
    return conn_data


def _kick_user(access_token):
    response = requests.get(
        f"{KICK_API}/users",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=6,
    )
    data = response.json()
    if response.status_code >= 400:
        raise RuntimeError(f"Kick /users HTTP {response.status_code}: {data}")
    users = data.get("data") or []
    if not users:
        raise RuntimeError("Kick /users não retornou o usuário autenticado.")
    return users[0]


def _usable_profile_picture(url):
    """Retorna uma foto real, ignorando fallbacks conhecidos da Kick."""
    value = str(url or "").strip()
    if not value:
        return ""
    lowered = value.lower()
    fallback_markers = (
        "default-profile-pictures",
        "default-avatar",
        "default2.jpeg",
        "default2.webp",
    )
    if any(marker in lowered for marker in fallback_markers):
        return ""
    return value


def _resolve_profile_picture(access_token, broadcaster_id, user=None, existing=""):
    """Resolve o avatar real sem alterar o fluxo de OAuth ou do bot."""
    user = user or {}
    direct = _usable_profile_picture(
        user.get("profile_picture")
        or user.get("profile_picture_url")
        or user.get("profile_pic")
        or user.get("avatar_url")
    )
    if direct:
        return direct

    stored = _usable_profile_picture(existing)
    if stored:
        return stored

    # Última tentativa com o próprio token OAuth: /users é a fonte oficial
    # do campo profile_picture e não depende de o canal estar ao vivo.
    try:
        response = requests.get(
            f"{KICK_API}/users",
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            params={"id": int(broadcaster_id)},
            timeout=6,
        )
        if response.status_code < 400:
            data = response.json() or {}
            for row in (data.get("data") or []):
                candidate = _usable_profile_picture(
                    row.get("profile_picture")
                    or row.get("profile_picture_url")
                    or row.get("avatar_url")
                )
                if candidate:
                    return candidate
    except Exception as exc:
        print(f"[KICK-PROFILE] consulta OAuth do avatar falhou: {exc}", flush=True)

    # Fallback público para instalações em que o token OAuth não devolve a
    # foto por alguma particularidade da sessão. Não depende da live estar ativa.
    try:
        app_token = _kick_app_access_token()
        if not app_token:
            return ""
        response = requests.get(
            f"{KICK_API}/users",
            headers={"Authorization": f"Bearer {app_token}", "Accept": "application/json"},
            params={"id": int(broadcaster_id)},
            timeout=6,
        )
        if response.status_code >= 400:
            return ""
        data = response.json() or {}
        for row in (data.get("data") or []):
            candidate = _usable_profile_picture(
                row.get("profile_picture")
                or row.get("profile_picture_url")
                or row.get("avatar_url")
            )
            if candidate:
                return candidate
    except Exception as exc:
        print(f"[KICK-PROFILE] fallback público do avatar falhou: {exc}", flush=True)
    return ""


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


def _expire_pending_bets(bid, platform="kick"):
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
                   AND platform=%s
                   AND status='pending'
                   AND expires_at < NOW()
                RETURNING challenger, defender, amount
                """,
                (int(bid), str(platform or "kick").lower()),
            )
            expired = cur.fetchall()
        conn.commit()
    finally:
        conn.close()
    return expired


def _format_balance(bid, user, platform="kick"):
    # !pontos é um dos comandos mais usados. Antes eram necessárias duas
    # consultas separadas para saldo/rank, além do ensure_player. Agora saldo
    # e posição saem de uma única leitura indexada do PostgreSQL.
    ch = get_channel(bid)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.points,
                       CASE WHEN p.points > 0 THEN
                           1 + (
                               SELECT COUNT(*)
                                 FROM players higher
                                WHERE higher.broadcaster_user_id=%s
                                  AND higher.platform=%s
                                  AND higher.points > 0
                                  AND higher.points > p.points
                           )
                       ELSE NULL END AS rank
                  FROM players p
                 WHERE p.broadcaster_user_id=%s
                   AND p.platform=%s
                   AND p.username=%s
                 LIMIT 1
                """,
                (int(bid), str(platform or "kick").lower(), int(bid), str(platform or "kick").lower(), user),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    points = int(row[0] or 0) if row else 0
    rank = int(row[1]) if row and row[1] is not None else None
    emoji = str(ch["currency_emoji"] or "").strip()
    return {
        "user": _mention(user),
        "points": points,
        "currency": ch["currency_name"],
        "emoji": emoji,
        "emoji_text": f" {emoji}" if emoji else "",
        "rank": rank if rank is not None else "",
        "rank_text": f" Sua posição no ranking é #{rank}." if rank is not None else "",
    }
def _format_ranking(bid, platform="kick"):
 ch=get_channel(bid);limit=max(1,min(int(ch['rank_limit']),10));c=get_conn()
 try:
  with c.cursor() as x:
   x.execute('SELECT username,points FROM players WHERE broadcaster_user_id=%s AND platform=%s AND points>0 ORDER BY points DESC,username ASC LIMIT %s',(int(bid),str(platform or "kick").lower(),limit))
   rows=x.fetchall()
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


def _process_chat(payload, send_chat=None):
    if send_chat is None:
        send_chat = lambda _bid, message: _send_chat(kick_bid, message)
    broadcaster = payload.get("broadcaster") or {}
    sender = payload.get("sender") or {}
    platform = str(payload.get("platform") or "kick").strip().lower()
    try:
        kick_bid = int(broadcaster.get("user_id"))
    except (TypeError, ValueError):
        print(f"[CHAT] broadcaster ID inválido: {payload}", flush=True)
        return

    # A Kick usa IDs numéricos e pode armazená-los em players.kick_user_id.
    # YouTube usa channel IDs como "UC..." e Twitch também pode usar IDs
    # externos que não são BIGINT. O motor de comandos identifica esses
    # usuários por plataforma + username, então não tente converter esses
    # IDs para int. Essa conversão era o motivo pelo qual o YouTube chegava
    # ao _process_chat e era descartado antes de executar qualquer comando.
    uid = None
    if platform == "kick" and sender.get("user_id"):
        try:
            uid = int(sender.get("user_id"))
        except (TypeError, ValueError):
            print(f"[KICK-CHAT] ID de usuário Kick inválido: {sender.get('user_id')!r}", flush=True)
            return

    # Integrações diferentes podem entregar o ID externo da plataforma,
    # enquanto o motor de comandos trabalha com o ID interno do perfil SN7.
    # O Twitch já resolve esse vínculo antes de chegar aqui, então preservamos
    # explicitamente o perfil para não tentar procurar uma conexão Kick.
    profile_id = payload.get("sn7_profile_id")
    if profile_id is not None:
        try:
            bid = int(profile_id)
        except (TypeError, ValueError):
            print(f"[CHAT] sn7_profile_id inválido: {profile_id!r}", flush=True)
            return
    else:
        conn_for_channel = _get_connection(kick_bid)
        bid = int(conn_for_channel.get("sn7_profile_id") if conn_for_channel else kick_bid)

    if platform not in {"kick", "twitch", "youtube"}:
        platform = "kick"
    user = str(sender.get("username") or sender.get("slug") or "").strip()
    content = str(payload.get("content") or "").strip()
    if not bid or not user:
        return

    # A presença individual é específica da Kick. Twitch/YouTube usam o
    # mesmo motor de comandos, mas não devem consultar a API da Kick.
    platform = str(payload.get("platform") or "kick").lower()
    # A consulta /livestreams da Kick pode levar até 10s e não é necessária
    # para responder comandos. Presença continua sendo verificada em mensagens
    # normais, mas comandos não ficam esperando a API de live.
    if platform == "kick" and not content.startswith("!") and _kick_channel_is_live(kick_bid):
        try:
            presence_bonus = award_watch_presence(bid, user, uid, "kick")
            if presence_bonus:
                print(f"[SN7-REWARDS] {user} +{presence_bonus} por presença em live", flush=True)
        except Exception as exc:
            print(f"[SN7-REWARDS] erro presença: {exc}", flush=True)

    if not content.startswith("!"):
        return

    # Parseia o comando antes de cadastrar/atualizar o participante. Os controles
    # da corrida não precisam de ensure_player, então evitamos uma ida ao banco
    # no caminho crítico de !corrida/!fimcrr/!finalizacorrida.
    pieces = content.split()
    cmd = pieces[0].lower()
    args = pieces[1:]

    # Corrida: !car1 até !car100 são comandos dinâmicos e não precisam existir
    # como 100 registros separados no painel. A janela de entrada é de 90s.
    car_match = re.fullmatch(r"!car(\d{1,3})", cmd)
    if car_match:
        try:
            car_number = int(car_match.group(1))
            if not 1 <= car_number <= 100:
                send_chat(bid, "🏎️ Escolha um carro de 1 a 100. Ex.: !car7.")
                return
            from core.minigames import race_join_car, race_begin
            result = race_join_car(bid, user, car_number, platform)
            if result.get("expired"):
                begun = race_begin(bid, platform)
                if begun.get("ok"):
                    _send_chat(bid, "🏁🏎️ As inscrições acabaram! A corrida começou! 3 capítulos e o resultado final!")
                    _schedule_race_story(bid, platform, STORY_CHAPTER_DELAY_SECONDS)
                else:
                    send_chat(bid, result.get("error", "🏁 O tempo para entrar acabou."))
                return
            if not result.get("ok"):
                send_chat(bid, result.get("error", "🏎️ Não foi possível entrar na corrida."))
                return
            send_chat(bid, result.get("message") or f"🏎️ {_mention(user)} escolheu o carro {car_number}!")
            return
        except Exception as exc:
            print(f"[RACE-CAR] erro: {exc}", flush=True)
            send_chat(bid, "🏎️ Erro ao registrar seu carro.")
            return

    try:
        cfg = find_command(bid, cmd)
        print(
            f"[KICK-CHAT] {user}: {content} -> "
            f"{cfg['command_key'] if cfg else 'não encontrado'}",
            flush=True,
        )
        if not cfg:
            return
        if not cfg["enabled"]:
            if cfg.get("category") == "minigames":
                send_chat(bid, f"🎮 O Mini Game {cfg.get('command') or cmd} está desativado nesta live.")
            return

        key = cfg["command_key"]
        # !corrida e os controles administrativos não precisam cadastrar o
        # usuário antes de responder. Isso reduz consultas no caminho crítico.
        if key not in {"race", "race_finish", "race_reset"}:
            ensure_channel(
                bid,
                str(broadcaster.get("username") or broadcaster.get("channel_slug") or ""),
            )
            ensure_player(bid, user, uid, platform)

        # Mini Games têm um interruptor global. Isso evita que um comando
        # seja reativado isoladamente enquanto o recurso inteiro está desligado.
        ismod = _is_moderator(sender, bid)

        if cfg["category"] == "minigames" and key != "race":
            try:
                from core.minigames import get_settings
                mini_settings = get_settings(bid, platform)
                if not mini_settings.get("enabled", True):
                    send_chat(bid, "🎮 Os Mini Games estão desativados nesta live.")
                    return
                game_key = "slots" if key == "slots" else "bets" if key in {"duel", "bet_accept", "bet_decline"} else None
                if game_key and not mini_settings.get(f"{game_key}_enabled", True):
                    return
            except Exception as exc:
                # Não silencie o comando. O handler específico continua e o
                # tratamento final informa a falha ao chat, além de registrar o erro.
                print(f"[MINIGAMES] erro verificando status global: {exc}", flush=True)
                mini_settings = None

        ch = get_channel(bid)

        # Todos os Mini Games compartilham estas funções. O import fica fora
        # dos blocos individuais para que !roubar, !sobreviver, !corrida etc.
        # nunca dependam de outro comando ter sido executado antes.
        if cfg.get("category") == "minigames" or key in {"poll_close", "race_finish", "race_reset", "survival_on", "survival_finish"}:
            from core.minigames import (
                play_coinflip, start_poll, vote_poll, close_poll,
                race_start, race_join_car, race_begin, race_finish, race_reset, target_guess, secret_guess,
                survival_start, survival_join, survival_finish,
                survival_begin, survival_tick,
                register_survival_timer, clear_survival_timer,
                steal_points, vault_play, jackpot_play,
                _runtime_get, _runtime_set, _adjust_points,
            )

        # Expiração de apostas só é necessária quando uma ação de aposta
        # acontece. Nunca bloqueie comandos comuns como !pontos com uma
        # consulta/commit extra no PostgreSQL.
        if key in {"duel", "bet_accept", "bet_decline"}:
            try:
                expired_bets = _expire_pending_bets(bid, platform)
                for challenger, defender, amount in expired_bets:
                    send_chat(
                        bid,
                        f"⏰ A aposta entre {_mention(challenger)} e {_mention(defender)} expirou após 90 segundos. Nenhum ponto foi removido.",
                    )
            except Exception as exc:
                print(f"[KICK-BET] erro expirando apostas: {exc}", flush=True)

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

            send_chat(bid, response)
            return

        currency = str(ch["currency_name"])
        emoji = str(ch["currency_emoji"])

        if key in {"addmusic", "skipmusic", "musicqueue", "nowplaying", "pausemusic", "resumemusic", "clearmusic"}:
            from core.music import add_from_chat, clear_queue, current_and_queue, set_playing, skip_current

            if key == "addmusic":
                query = " ".join(args).strip()
                if not query:
                    send_chat(bid, f"🎵 Use {cfg['command']} artista - música ou envie um link.")
                    return
                try:
                    item, position = add_from_chat(bid, query, user)
                    response = _render_response(cfg["response"], {
                        "user": _mention(user),
                        "music": item["title"],
                        "queue_position": position
                    })
                    send_chat(bid, response)
                except ValueError as exc:
                    send_chat(bid, f"❌ {exc}")
                return

            if key == "skipmusic":
                current = skip_current(bid)
                send_chat(bid, _render_response(cfg["response"], {
                    "music": current["title"] if current else "nenhuma música"
                }))
                return

            if key == "musicqueue":
                current, queue = current_and_queue(bid)
                parts = []
                if current:
                    parts.append(f"▶ {current['title']}")
                for i, item in enumerate(queue[:4], 1):
                    parts.append(f"{i}. {item['title']}")
                queue_text = "🎵 Fila vazia." if not parts else "🎵 " + " • ".join(parts)
                send_chat(bid, _render_response(cfg["response"], {"queue": queue_text}))
                return

            if key == "nowplaying":
                current, _ = current_and_queue(bid)
                music_text = current["title"] if current else "nenhuma música tocando"
                send_chat(bid, _render_response(cfg["response"], {"music": music_text}))
                return

            if key == "pausemusic":
                set_playing(bid, False)
                send_chat(bid, cfg["response"] or "⏸️ Música pausada.")
                return

            if key == "resumemusic":
                set_playing(bid, True)
                send_chat(bid, cfg["response"] or "▶️ Música retomada.")
                return

            if key == "clearmusic":
                clear_queue(bid)
                send_chat(bid, cfg["response"] or "🧹 Fila de músicas limpa.")
                return

        if key in {"coinflip", "coinflip_coroa"}:
            choice = "cara" if key == "coinflip" else "coroa"
            if key == "coinflip":
                if len(args) >= 2 and args[0].lower() in {"cara","coroa"}: choice=args[0].lower(); amount=args[1]
                elif args: amount=args[0]
                else:
                    send_chat(bid, f"🪙 Use {cfg['command']} quantidade. Ex.: {cfg['command']} 20"); return
            else:
                amount=args[0] if args else None
                if not amount: send_chat(bid, f"🪙 Use {cfg['command']} quantidade. Ex.: {cfg['command']} 20"); return
            try: amount=int(str(amount).replace('.','').replace(',',''))
            except ValueError: send_chat(bid, "🪙 Valor inválido."); return
            result=play_coinflip(bid,user,choice,amount,platform,uid)
            if not result.get("ok"): send_chat(bid,result["error"]); return
            icon="🪙" if result["result"]=="cara" else "👑"
            text=f"{icon} Saiu {result['result']}! " + (f"🎉 Você venceu +{result['payout']} {currency}." if result['payout'] else f"💥 Você perdeu {result['amount']} {currency}.")
            send_chat(bid,_render_response(cfg["response"],{"user":_mention(user),"choice":choice,"coinflip_result":text,"new_points":result["points"],"currency":currency})); return

        if key == "poll":
            raw=" ".join(args); parts=[x.strip() for x in raw.split("|")]
            result=start_poll(bid,user,parts[0] if parts else "",parts[1:] if len(parts)>1 else [],platform)
            send_chat(bid, result.get("error") or f"📊 Enquete aberta: {result['state']['question']} — " + " | ".join(f"{i+1}) {o}" for i,o in enumerate(result['state']['options']))); return
        if key == "vote":
            result=vote_poll(bid,user," ".join(args),platform); send_chat(bid,result.get("error") or f"📊 {_mention(user)} votou em {result['option']}."); return
        if key == "poll_close":
            result=close_poll(bid,platform)
            if not result.get("ok"): send_chat(bid,result["error"]); return
            st=result["state"]; counts=result["counts"]; send_chat(bid,"📊 Resultado: " + " • ".join(f"{o}: {counts[i]}" for i,o in enumerate(st["options"]))); return

        if key in {"quiz","quiz_answer"}:
            questions=[("Qual é o maior planeta do Sistema Solar?", "jupiter"),("Quantos lados tem um hexágono?", "6"),("Qual é a capital do Brasil?", "brasilia")]
            state=_runtime_get(bid,platform,"quiz",{})
            if key=="quiz":
                q,a=random.choice(questions); state={"open":True,"question":q,"answer":a}; _runtime_set(bid,platform,"quiz",state); send_chat(bid,f"🧠 QUIZ: {q} Use {find_command(bid,'!resposta')['command'] if find_command(bid,'!resposta') else '!resposta'} sua resposta!"); return
            if not state.get("open"): send_chat(bid,"🧠 Não há quiz aberto."); return
            answer=" ".join(args).strip().lower().replace("á","a").replace("ã","a").replace("é","e").replace("í","i").replace("ó","o").replace("ú","u").replace(" ","")
            if answer==state.get("answer"):
                _adjust_points(bid,user,300,platform,uid); state["open"]=False; _runtime_set(bid,platform,"quiz",state); send_chat(bid,f"🧠 🎉 {_mention(user)} acertou e ganhou 300 {currency}!");
            else: send_chat(bid,f"🧠 {_mention(user)} errou. Tente novamente!")
            return

        if key == "race":
            if not ismod:
                send_chat(bid, "⛔ Apenas streamer/mod pode iniciar a corrida.")
                return
            result = race_start(bid, user, platform)
            if not result.get("ok"):
                send_chat(bid, result.get("error") or "🏁 Não foi possível abrir a corrida.")
                return
            send_chat(
                bid,
                "🏁🏎️ CORRIDA ABERTA! Você tem 90s para entrar. Digite !car1 até !car100 para escolher seu carro. "
                "Depois dos 90s, quem entrou entrou: começam 3 capítulos e o resultado!",
            )
            _schedule_join_timer(_RACE_JOIN_TIMERS, bid, platform, _start_race_after_join_window, "RACE-JOIN")
            return
        if key == "race_finish":
            if not ismod:
                send_chat(bid, "⛔ Apenas streamer/mod pode finalizar a corrida.")
                return
            result = race_finish(bid, platform)
            _cancel_story_timer(_RACE_JOIN_TIMERS, bid, platform)
            _cancel_story_timer(_RACE_STORY_TIMERS, bid, platform)
            if not result.get("ok"):
                send_chat(bid,result["error"]); return
            winners=result.get("winners") or []
            send_chat(bid,"🏁 FIM DA CORRIDA! " + (" • ".join(f"{i+1}º {_mention(u)} +{prize} {currency}" for i,(u,prize) in enumerate(winners)) if winners else "Ninguém chegou ao final."))
            return
        if key == "race_reset":
            if not ismod:
                send_chat(bid, "⛔ Apenas streamer/mod pode resetar a corrida.")
                return
            result = race_reset(bid, platform)
            _cancel_story_timer(_RACE_JOIN_TIMERS, bid, platform)
            _cancel_story_timer(_RACE_STORY_TIMERS, bid, platform)
            if not result.get("ok"):
                send_chat(bid, result["error"])
                return
            count = len(result.get("players") or [])
            send_chat(bid, f"🛑 Corrida resetada! {count} participante(s) foram removidos. Use !corrida para iniciar outra.")
            return
        if key == "target":
            result=target_guess(bid,user,args[0] if args else "",platform)
            if not result.get("ok"): send_chat(bid,result["error"]); return
            send_chat(bid, f"🎯 🎉 {_mention(user)} acertou o alvo e ganhou 300 {currency}!" if result.get("win") else f"🎯 {_mention(user)} ficou a {result['distance']} do alvo."); return
        if key == "secret":
            result=secret_guess(bid,user,args[0] if args else "",platform)
            if not result.get("ok"): send_chat(bid,result["error"]); return
            send_chat(bid, f"🔢 🎉 {_mention(user)} descobriu o número e ganhou 500 {currency}!" if result.get("win") else f"🔢 Tente um número {result['hint']}."); return
        if key == "survival_on":
            if not ismod:
                send_chat(bid, "⛔ Apenas streamer/mod pode iniciar a sobrevivência.")
                return
            result = survival_start(bid, user, platform)
            if not result.get("ok"):
                send_chat(bid, result.get("error") or "🧟 Não foi possível abrir a sobrevivência.")
                return
            send_chat(
                bid,
                "🧟 SOBREVIVÊNCIA ABERTA! Você tem 90s para entrar. Digite !sobreviver. "
                "Depois dos 90s, quem entrou entrou: começam 3 capítulos e o resultado!",
            )
            _schedule_join_timer(_SURVIVAL_JOIN_TIMERS, bid, platform, _start_survival_after_join_window, "SURVIVAL-JOIN")
            return

        if key == "survival":
            result = survival_join(bid, user, platform)
            if result.get("expired"):
                begun = survival_begin(bid, platform)
                if begun.get("ok"):
                    send_chat(bid, "🧟 As inscrições acabaram! A história começou! 3 capítulos e o resultado final!")
                    _schedule_survival_story(bid, platform, STORY_CHAPTER_DELAY_SECONDS)
                else:
                    send_chat(bid, result.get("error") or "🧟 O tempo para entrar acabou.")
                return
            if not result.get("ok"):
                send_chat(bid, result.get("error") or "🧟 Não foi possível entrar.")
                return
            send_chat(bid, result.get("message") or f"🧟 {_mention(user)} entrou na sobrevivência!")
            return

        if key == "survival_finish":
            if not ismod:
                send_chat(bid, "⛔ Apenas streamer/mod pode finalizar a sobrevivência.")
                return
            clear_survival_timer(bid, platform)
            _cancel_story_timer(_SURVIVAL_JOIN_TIMERS, bid, platform)
            _cancel_story_timer(_SURVIVAL_STORY_TIMERS, bid, platform)
            result = survival_finish(bid, platform)
            if not result.get("ok"):
                send_chat(bid, result["error"])
                return
            winners = result.get("winners") or []
            if winners:
                send_chat(bid, "🧟 Sobreviveram: " + " • ".join(f"{_mention(u)} +{prize} {currency}" for u,prize in winners))
            else:
                send_chat(bid, "🧟 Sobrevivência finalizada. Ninguém entrou na rodada.")
            return
        if key == "steal":
            if not args or not str(args[0]).strip().lstrip("@"): 
                send_chat(bid, f"💰 Use {cfg['command']} @usuário")
                return
            target=args[0].lstrip("@").strip()
            result=steal_points(bid,user,target,platform,uid)
            if not result.get("ok"): send_chat(bid,result["error"]); return
            send_chat(bid, f"💰 {_mention(user)} roubou {result['amount']} {currency}! Saldo: {result['points']} {currency}." if result.get("win") else f"💨 {_mention(user)} tentou roubar, mas falhou!"); return
        if key == "vault":
            result=vault_play(bid,user,args[0] if args else "",platform)
            if not result.get("ok"): send_chat(bid,result["error"]); return
            send_chat(bid, f"🔐 🎉 {_mention(user)} abriu o cofre e ganhou 400 {currency}!" if result.get("win") else f"🔐 O cofre não abriu. Tente novamente!"); return
        if key == "jackpot":
            result=jackpot_play(bid,user,platform,uid)
            if not result.get("ok"): send_chat(bid,result["error"]); return
            send_chat(bid, f"👑 🎉 {_mention(user)} ganhou {result['prize']} {currency} do Jackpot! Saldo: {result['points']} {currency}." if result.get("win") else f"👑 O Jackpot não saiu desta vez!"); return

        if key == "slots":
            if not args:
                send_chat(bid, f"🎰 Use {cfg['command']} quantidade. Ex.: {cfg['command']} 100")
                return
            try:
                amount = int(str(args[0]).replace(".", "").replace(",", ""))
            except ValueError:
                send_chat(bid, f"🎰 Quantidade inválida. Ex.: {cfg['command']} 100")
                return

            try:
                from core.minigames import play_slots
                result = play_slots(bid, user, amount, platform, uid)
            except Exception as exc:
                print(f"[SLOTS] erro: {exc}", flush=True)
                send_chat(bid, "🎰 O cassino está indisponível agora. Tente novamente em alguns segundos.")
                return

            if not result.get("ok"):
                send_chat(bid, result.get("error") or "🎰 Não foi possível jogar agora.")
                return

            outcome = result.get("outcome")
            symbols = result.get("symbols", "🎰 🎰 🎰")
            if outcome == "pair":
                slots_result = f"{symbols} 🟡 2 iguais! +{result['payout']} {currency}"
            elif outcome == "pair_special":
                slots_result = f"{symbols} 🟠 2 símbolos! +{result['payout']} {currency}"
            elif outcome == "diamond":
                slots_result = f"{symbols} 💎 VITÓRIA! +{result['profit']} {currency}"
            elif outcome == "jackpot":
                slots_result = f"{symbols} 👑 JACKPOT! +{result['profit']} {currency}"
            elif outcome == "triple":
                slots_result = f"{symbols} 🎉 TRÊS IGUAIS! +{result['profit']} {currency}"
            else:
                slots_result = f"{symbols} 💥 Perdeu {result['amount']} {currency}"

            values = {
                "user": _mention(user),
                "amount": result["amount"],
                "currency": currency,
                "slots_result": slots_result,
                "new_points": result["points"],
                "house": result["house"],
            }
            send_chat(bid, _render_response(cfg["response"], values))
            if result.get("refill"):
                hours = result.get("refill_hours") or 1
                send_chat(bid, f"⏱️ O cassino recebeu +{result['refill']} {currency} por {hours}h de live. Banco: {result['house']} {currency}.")
            return

        if key == "points":
            send_chat(bid, _render_response(cfg["response"], _format_balance(bid, user, platform)))
            return

        if key == "ranking":
            send_chat(bid, _render_response(cfg["response"], {"ranking": _format_ranking(bid, platform)}))
            return

        if key == "cmds":
            send_chat(bid, _render_response(cfg["response"], {"commands": _commands_text(bid)}))
            return

        if key == "duel":
            # !aposta @usuario quantidade cria uma aposta pendente.
            command_values = _extract_command_values(args)
            target_value = str(command_values.get("target") or "").strip().lstrip("@")
            amount_value = command_values.get("amount")

            if not target_value or amount_value is None or int(amount_value) <= 0:
                send_chat(
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
                send_chat(
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

            ensure_player(bid, target_value, platform=platform)

            conn = get_conn()
            try:
                with conn.cursor() as cur:
                    # Remove apostas expiradas do mesmo canal.
                    cur.execute(
                        "DELETE FROM pending_bets WHERE broadcaster_user_id=%s AND platform=%s AND (status<>'pending' OR expires_at<NOW())",
                        (bid, platform),
                    )

                    cur.execute(
                        """
                        SELECT id FROM pending_bets
                         WHERE broadcaster_user_id=%s
                           AND platform=%s
                           AND status='pending'
                           AND (challenger=%s OR defender=%s OR challenger=%s OR defender=%s)
                         LIMIT 1
                        """,
                        (bid, platform, user, user, target_value, target_value),
                    )
                    existing = cur.fetchone()
                    if existing:
                        send_chat(
                            bid,
                            f"⚔️ Já existe uma aposta pendente envolvendo {_mention(user)} ou {_mention(target_value)}."
                        )
                        return

                    cur.execute(
                        """
                        SELECT points FROM players
                         WHERE broadcaster_user_id=%s AND platform=%s AND username=%s
                        FOR UPDATE
                        """,
                        (bid, platform, user),
                    )
                    row = cur.fetchone()
                    current_points = int(row[0] or 0) if row else 0

                    if current_points < amount_value:
                        send_chat(
                            bid,
                            f"❌ {_mention(user)} não tem {amount_value} {currency} para apostar. Saldo: {current_points}."
                        )
                        return

                    cur.execute(
                        """
                        INSERT INTO pending_bets
                            (broadcaster_user_id, platform, challenger, defender, amount)
                        VALUES (%s,%s,%s,%s,%s)
                        RETURNING id
                        """,
                        (bid, platform, user, target_value, amount_value),
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
            send_chat(bid, _render_response(cfg["response"], duel_values))
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
                           AND platform=%s
                           AND defender=%s
                           AND status='pending'
                           AND expires_at>=NOW()
                         ORDER BY created_at DESC
                         LIMIT 1
                         FOR UPDATE
                        """,
                        (bid, platform, user),
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
                               AND platform=%s
                               AND username IN (%s,%s)
                             FOR UPDATE
                            """,
                            (bid, platform, challenger, defender),
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
                                 WHERE broadcaster_user_id=%s AND platform=%s AND username=%s
                                """,
                                (amount, bid, platform, winner),
                            )
                            cur.execute(
                                """
                                UPDATE players
                                   SET points=GREATEST(0,points-%s), duels=duels+1, updated_at=NOW()
                                 WHERE broadcaster_user_id=%s AND platform=%s AND username=%s
                                """,
                                (amount, bid, platform, loser),
                            )
                            cur.execute(
                                "UPDATE pending_bets SET status='accepted' WHERE id=%s",
                                (bet_id,),
                            )
                            cur.execute(
                                """
                                INSERT INTO duel_events
                                    (broadcaster_user_id,platform,attacker,defender,winner,
                                     winner_points_delta,loser_points_delta)
                                VALUES (%s,%s,%s,%s,%s,%s,%s)
                                """,
                                (bid, platform, challenger, defender, winner, amount, -amount),
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

            forget_rankings(bid)

            if first_message:
                send_chat(
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
                send_chat(bid, result_message)
            else:
                send_chat(
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

        if key in {"addcmd", "addpoint", "settpoint", "delcmd", "poll_close", "race_finish", "race_reset", "survival_on", "survival_finish"} and not ismod:
            send_chat(bid, "⛔ Apenas streamer/mod pode usar este comando.")
            return

        if key == "addcmd":
            if len(args) < 2:
                send_chat(bid, "Use !addcmd !comando resposta")
                return

            custom = args[0].lower()
            custom = custom if custom.startswith("!") else "!" + custom
            resp = " ".join(args[1:]).strip()
            if len(custom) > 64:
                send_chat(bid, "❌ Comando muito longo.")
                return
            if not resp:
                send_chat(bid, "❌ Informe uma resposta.")
                return

            all_commands = list_commands(bid)
            used = {x["command"] for x in all_commands}
            used.update(alias for x in all_commands for alias in x["aliases"])
            if custom in used:
                send_chat(bid, "⛔ Essa palavra de ativação já está em uso.")
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

            send_chat(
                bid,
                _render_response(cfg["response"], {"command": custom}),
            )
            return

        if key in {"addpoint", "settpoint"}:
            if len(args) < 2:
                send_chat(bid, f'Use {cfg["command"]} @usuário quantidade')
                return

            target = args[0].lstrip("@").strip()
            try:
                amount = int(args[1])
                amount = max(0, amount) if key == "settpoint" else amount
            except ValueError:
                send_chat(bid, "❌ Quantidade inválida.")
                return

            if key == "addpoint" and amount <= 0:
                send_chat(bid, "❌ A quantidade precisa ser maior que 0.")
                return

            ensure_player(bid, target, platform=platform)
            conn = get_conn()
            try:
                with conn.cursor() as cur:
                    expression = "points+%s" if key == "addpoint" else "%s"
                    cur.execute(
                        f"""
                        UPDATE players
                           SET points={expression}, updated_at=NOW()
                         WHERE broadcaster_user_id=%s AND platform=%s AND username=%s
                         RETURNING points
                        """,
                        (amount, bid, platform, target),
                    )
                    row = cur.fetchone()
                conn.commit()
            finally:
                conn.close()

            forget_rankings(bid)

            new_points = int(row[0]) if row else amount
            send_chat(
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
                send_chat(bid, "Use !delcmd !comando")
                return

            target = find_command(bid, args[0].lower())
            if not target or target["is_system"]:
                send_chat(bid, "❌ Esse comando personalizado não existe.")
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

            send_chat(
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

            send_chat(
                bid,
                _render_response(cfg["response"], custom_values, args=args)
            )

    except Exception as exc:
        print(f"[KICK-CHAT] erro processando {content!r}: {exc}", flush=True)
        try:
            if cfg and (cfg.get("category") == "minigames" or key in {"poll_close", "race_finish", "race_reset", "survival_on", "survival_finish"}):
                send_chat(bid, "🎮 Não consegui processar esse Mini Game agora. Tente novamente em alguns segundos.")
        except Exception:
            pass

def _remember_recent_chat(payload):
    """Deduplica uma mensagem mesmo quando IDs de webhook diferem."""
    broadcaster = payload.get("broadcaster") or {}
    sender = payload.get("sender") or {}
    try:
        bid = int(broadcaster.get("user_id") or 0)
    except (TypeError, ValueError):
        bid = 0
    try:
        uid = int(sender.get("user_id") or 0)
    except (TypeError, ValueError):
        uid = 0
    content = str(payload.get("content") or "").strip().lower()
    key = (bid, uid, content)
    now = time.monotonic()
    with _recent_chat_events_lock:
        for old_key, expires in list(_recent_chat_events.items()):
            if expires <= now:
                _recent_chat_events.pop(old_key, None)
        already_seen = key in _recent_chat_events and _recent_chat_events[key] > now
        _recent_chat_events[key] = now + _recent_chat_events_ttl
        return already_seen


def _process_webhook(payload, event_type):
    print(f"[KICK-WEBHOOK] evento recebido para processamento: {event_type}", flush=True)

    # Bootstrap/migration acontece fora do request HTTP. A Kick recebe 200
    # imediatamente, enquanto este worker prepara o PostgreSQL para o comando.
    try:
        init_db()
        from core.minigames import ensure_minigame_table
        ensure_minigame_table()
    except Exception as exc:
        print(f"[KICK-WEBHOOK] falha no bootstrap do banco: {exc}", flush=True)
        return
    if event_type == "chat.message.sent":
        print("[KICK-WEBHOOK] processando chat.message.sent", flush=True)
        # A chegada de um chat.message.sent é uma prova direta de que o bot
        # está recebendo eventos. Atualizamos o cache para a UI não mostrar
        # "desligado" enquanto a assinatura já está entregando mensagens.
        try:
            broadcaster = payload.get("broadcaster") or {}
            bid = int(broadcaster.get("user_id") or 0)
            if bid > 0:
                conn_for_channel = _get_connection(bid)
                cache_bid = int(conn_for_channel.get("sn7_profile_id") if conn_for_channel else bid)
                # Evita uma segunda consulta à conexão dentro do _process_chat.
                payload["sn7_profile_id"] = cache_bid
                _bot_status_cache[cache_bid] = (time.time(), True)
        except (TypeError, ValueError):
            pass
        if _remember_recent_chat(payload):
            print("[KICK-WEBHOOK] mensagem duplicada detectada no cache; ignorando", flush=True)
            return
        _process_chat(payload)
        return

    broadcaster = payload.get("broadcaster") or {}
    try:
        bid = int(broadcaster.get("user_id"))
    except (TypeError, ValueError):
        return
    conn_for_channel = _get_connection(bid)
    bid = int(conn_for_channel.get("sn7_profile_id") if conn_for_channel else bid)
    rewards = get_point_rewards(bid)

    if event_type in {"channel.subscription.new", "channel.subscription.renewal"}:
        subscriber = payload.get("subscriber") or {}
        username = str(subscriber.get("username") or subscriber.get("slug") or "").strip()
        uid = subscriber.get("user_id")
        if username and rewards["sub_bonus"] > 0:
            add_points(bid, username, rewards["sub_bonus"], uid, "kick")
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
            add_points(bid, username, bonus, uid, "kick")
            print(f"[SN7-REWARDS] {username} +{bonus} por {amount} KICK(s)", flush=True)


def _session_broadcaster_id(validate=True):
    try:
        value = get_session_broadcaster_id(validate=validate)
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


def _save_bot_state(profile_id, active):
    """Persiste o estado do bot pelo perfil SN7 ou pelo ID real da Kick."""
    try:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE kick_connections
                          SET bot_active=%s, updated_at=NOW()
                        WHERE sn7_profile_id=%s OR broadcaster_user_id=%s""",
                    (bool(active), int(profile_id), int(profile_id)),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        print(f"[KICK-BOT] não foi possível persistir estado: {exc}", flush=True)


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
        _save_bot_state(bid, active)
    return active



def _save_profile_picture(broadcaster_id, url):
    conn_data=_get_connection(int(broadcaster_id))
    if not conn_data:return
    conn=get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE kick_connections SET profile_picture_url=%s,updated_at=NOW() WHERE broadcaster_user_id=%s",(url,int(conn_data["broadcaster_user_id"])))
        conn.commit()
    finally: conn.close()

def _save_username(broadcaster_id, username):
    conn_data=_get_connection(int(broadcaster_id))
    if not conn_data:return
    conn=get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE kick_connections SET username=%s,updated_at=NOW() WHERE broadcaster_user_id=%s",(username,int(conn_data["broadcaster_user_id"])))
        conn.commit()
    finally: conn.close()

def _safe_remote_image_url(url):
    """Valida uma URL de imagem remota antes de o servidor fazer proxy."""
    value = str(url or "").strip()
    try:
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.hostname:
            return ""
        host = parsed.hostname.lower().rstrip(".")
        try:
            infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            for info in infos:
                ip = ipaddress.ip_address(info[4][0])
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                    return ""
        except Exception:
            return ""
        return value
    except Exception:
        return ""


@kick_bp.get("/profile/avatar")
def profile_avatar():
    """Entrega o avatar real da Kick pela mesma origem do painel."""
    bid = _session_broadcaster_id()
    if bid is None:
        return jsonify({"ok": False, "error": "Faça login com a Kick primeiro."}), 401

    try:
        # Renova o OAuth quando necessário e, principalmente, não confia em
        # uma URL de avatar antiga salva no banco. Isso corrige sessões antigas
        # que ficaram presas no avatar padrão da Kick.
        conn = _valid_connection(int(bid))
        if not conn or not conn.get("access_token"):
            return jsonify({"ok": False, "error": "A sessão da Kick expirou. Entre novamente."}), 401

        snapshot = session.get("kick_profile") if isinstance(session.get("kick_profile"), dict) else {}
        avatar = _resolve_profile_picture(
            conn["access_token"],
            int(conn["broadcaster_user_id"]),
            user=None,
            existing=(
                snapshot.get("profile_picture_url")
                or conn.get("profile_picture_url")
                or ""
            ),
        )
        if not avatar:
            return jsonify({"ok": False, "error": "Avatar da Kick não disponível."}), 404

        # Persiste o endereço real para as próximas aberturas do painel.
        _save_profile_picture(int(bid), avatar)
        if snapshot:
            snapshot = dict(snapshot)
            snapshot["profile_picture_url"] = avatar
            session["kick_profile"] = snapshot

        url = _safe_remote_image_url(avatar)
        if not url:
            return jsonify({"ok": False, "error": "Avatar da Kick não disponível."}), 404

        response = requests.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; SN7-Core/1.9.2)",
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                "Referer": "https://kick.com/",
            },
            timeout=8,
            allow_redirects=True,
        )
        if response.status_code >= 400:
            return jsonify({"ok": False, "error": "A imagem do perfil não pôde ser carregada."}), 502

        if not _safe_remote_image_url(response.url):
            return jsonify({"ok": False, "error": "Redirecionamento de imagem inválido."}), 502

        content_type = str(response.headers.get("Content-Type") or "").split(";")[0].lower()
        body = response.content
        magic_ok = (
            body.startswith(b"\x89PNG\r\n\x1a\n") or
            body.startswith(b"\xff\xd8\xff") or
            (body.startswith(b"RIFF") and body[8:12] == b"WEBP") or
            body.startswith(b"GIF8")
        )
        if not content_type.startswith("image/") and not magic_ok:
            return jsonify({"ok": False, "error": "A Kick não retornou uma imagem válida."}), 502
        if len(body) > 8 * 1024 * 1024:
            return jsonify({"ok": False, "error": "Imagem do perfil muito grande."}), 502

        mimetype = content_type[6:] if content_type.startswith("image/") else "webp"
        return Response(
            body,
            status=200,
            mimetype=mimetype,
            headers={
                "Cache-Control": "private, max-age=3600",
                "X-Content-Type-Options": "nosniff",
            },
        )
    except Exception as exc:
        print(f"[KICK-PROFILE] proxy do avatar falhou broadcaster={bid}: {exc}", flush=True)
        return jsonify({"ok": False, "error": "Não foi possível carregar a foto do perfil."}), 502

@kick_bp.get("/me")
def me():
    # /kick/me é somente leitura do perfil da sessão; não exige uma consulta
    # adicional ao banco para validar algo que já veio do OAuth.
    bid = _session_broadcaster_id(validate=False)
    if bid is None:
        return jsonify({"ok": True, "authenticated": False, "user": None, "bot": {"active": False}})
    cached = _profile_cache.get(int(bid))
    if cached and time.time() - cached[0] < _profile_cache_ttl:
        return jsonify(cached[1])
    # /kick/me nunca renova token nem chama a API externa. Isso mantém a primeira
    # pintura rápida mesmo quando o access_token está expirado. A atualização de
    # nome/foto, quando necessária, acontece em /kick/profile/refresh.
    try:
        conn = _get_connection(bid)
    except Exception as exc:
        # O perfil já autenticado pode continuar sendo exibido pelo snapshot da
        # sessão mesmo se o PostgreSQL estiver em cold start ou temporariamente
        # indisponível. O erro não deve transformar a tela em logout.
        print(f"[KICK-PROFILE] leitura do banco falhou: {exc}", flush=True)
        conn = None

    snapshot = session.get("kick_profile")
    if isinstance(snapshot, dict) and int(snapshot.get("id") or 0) == int(bid):
        username = str(snapshot.get("username") or "Kick").strip()
        profile_picture_url = str(snapshot.get("profile_picture_url") or "").strip()
    elif conn:
        username = str(conn.get("username") or "Kick").strip()
        profile_picture_url = str(conn.get("profile_picture_url") or "").strip()
    else:
        # A sessão OAuth continua sendo suficiente para manter o perfil
        # conectado na UI. O nome/foto serão recuperados assim que o banco
        # voltar, sem transformar um cold start em logout falso.
        username = "Kick"
        profile_picture_url = ""

    # /kick/me é crítico para a primeira pintura do Perfil.
    # Não bloqueia esperando uma chamada externa só para saber se o bot está ativo.
    result = {
        "ok": True,
        "authenticated": True,
        "user": {"id": int(bid), "username": username, "profile_picture_url": profile_picture_url},
        "bot": {"active": False},
    }
    session["kick_profile"] = result["user"]
    _profile_cache[int(bid)] = (time.time(), result)
    return jsonify(result)


@kick_bp.get("/profile/refresh")
def profile_refresh():
    """Atualiza nome/foto sem colocar a rede no caminho crítico do Perfil."""
    bid = _session_broadcaster_id()
    if bid is None:
        return jsonify({"ok": False, "authenticated": False, "error": "Entre com a Kick primeiro."}), 401
    conn = _valid_connection(bid)
    if not conn:
        return jsonify({"ok": False, "authenticated": True, "error": "A sessão da Kick expirou. Entre novamente."}), 401
    try:
        user = _kick_user(conn["access_token"])
        username = str(user.get("username") or user.get("slug") or user.get("channel_slug") or user.get("name") or user.get("display_name") or conn.get("username") or "Kick").strip()
        avatar = _resolve_profile_picture(
            conn["access_token"],
            bid,
            user=user,
            existing=conn.get("profile_picture_url") or "",
        )
        if username: _save_username(bid, username)
        if avatar: _save_profile_picture(bid, avatar)
        session["kick_profile"] = {"id": int(bid), "username": username, "profile_picture_url": avatar}
        result = {"ok": True, "authenticated": True, "user": {"id": int(bid), "username": username, "profile_picture_url": avatar}}
        _profile_cache[int(bid)] = (time.time(), result)
        return jsonify(result)
    except Exception as exc:
        print(f"[KICK-PROFILE] atualização falhou broadcaster={bid}: {exc}", flush=True)
        return jsonify({"ok": False, "authenticated": True, "error": "Não foi possível atualizar os dados do canal agora."}), 502


@kick_bp.get("/bot/status")
def bot_status():
    """Consulta o estado real do bot usando uma conexão Kick renovada."""
    bid = _session_broadcaster_id()
    if bid is None:
        return jsonify({"ok": True, "authenticated": False, "active": False})

    # Não usamos _get_connection() diretamente aqui. O access_token da Kick
    # pode ter expirado enquanto o webhook continua funcionando. Nesse caso,
    # consultar /events/subscriptions com o token antigo retornava 401 e a UI
    # interpretava isso incorretamente como "bot desligado".
    conn = _valid_connection(bid)
    if not conn or not conn.get("access_token"):
        return jsonify({"ok": True, "authenticated": False, "active": False})

    try:
        active = _bot_active(conn["access_token"], int(bid))
    except Exception as exc:
        print(f"[KICK-BOT] status falhou: {exc}", flush=True)
        # Se a consulta externa falhar, não inventamos "desligado". Mantemos
        # o último estado conhecido durante o TTL do cache.
        cached = _bot_status_cache.get(int(bid))
        if cached:
            active = bool(cached[1])
        else:
            active = bool(conn.get("bot_active"))
    return jsonify({"ok": True, "authenticated": True, "active": bool(active)})


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
            result = _subscribe_chat(conn["access_token"], int(conn["broadcaster_user_id"]))
        else:
            result = _unsubscribe_chat(conn["access_token"])
        _bot_status_cache[int(bid)] = (time.time(), bool(desired))
        _save_bot_state(bid, desired)
        return jsonify({"ok": True, "active": desired, "result": result})
    except Exception as exc:
        print(f"[KICK-BOT] toggle falhou broadcaster={bid}: {exc}", flush=True)
        return jsonify({"ok": False, "error": "Não foi possível alterar o status do bot."}), 502


@kick_bp.post("/<int:profile_id>/disconnect")
def disconnect_platform(profile_id):
    """Remove a conta Kick vinculada ao perfil SN7 sem trocar o perfil principal."""
    bid = _session_broadcaster_id()
    if bid is None or int(bid) != int(profile_id):
        return jsonify({"ok": False, "error": "Sessão inválida para este perfil."}), 401

    conn_data = _get_connection(profile_id)
    if not conn_data:
        return jsonify({"ok": False, "error": "Conta Kick não está conectada."}), 404

    # Desativa assinaturas do bot antes de remover os tokens.
    try:
        valid = _valid_connection(profile_id)
        if valid and valid.get("access_token") and valid.get("bot_active"):
            _unsubscribe_chat(valid["access_token"])
    except Exception as exc:
        print(f"[KICK-DISCONNECT] não foi possível remover assinaturas: {exc}", flush=True)

    db = get_conn()
    try:
        with db.cursor() as cur:
            cur.execute(
                "DELETE FROM kick_connections WHERE sn7_profile_id=%s OR broadcaster_user_id=%s",
                (int(profile_id), int(conn_data["broadcaster_user_id"])),
            )
            cur.execute(
                "DELETE FROM chat_connections WHERE broadcaster_user_id=%s AND provider='kick'",
                (int(profile_id),),
            )
            cur.execute(
                "SELECT COUNT(*) FROM chat_connections WHERE broadcaster_user_id=%s AND provider IN ('twitch','youtube')",
                (int(profile_id),),
            )
            remaining = int(cur.fetchone()[0] or 0)
        db.commit()
    finally:
        db.close()

    _bot_status_cache.pop(int(profile_id), None)
    _profile_cache.pop(int(profile_id), None)
    session.pop("kick_broadcaster_id", None)
    session.pop("kick_profile", None)

    # Se não houver outra plataforma vinculada, encerra a sessão do SN7.
    if remaining == 0:
        session.pop("sn7_broadcaster_id", None)

    return jsonify({"ok": True, "disconnected": True, "logged_out": remaining == 0})


@kick_bp.post("/logout")
def logout():
    # Rota legada mantida para compatibilidade. O logout principal agora
    # usa /api/session/logout, mas esta rota também precisa zerar a sessão
    # inteira para nunca deixar um cookie antigo reidratar o perfil.
    session.clear()
    session.modified = True
    return jsonify({"ok": True, "logged_out": True})


@kick_bp.get("/login")
def login():
    if not _client_id() or not _client_secret():
        return jsonify({"ok": False, "error": "KICK_CLIENT_ID/KICK_CLIENT_SECRET não configurados no Render."}), 503

    # Aceitamos somente um destino interno conhecido. Nunca redirecionamos
    # para uma URL fornecida livremente pelo navegador.
    next_page = request.args.get("next", "profile")
    if next_page != "profile":
        next_page = "profile"

    # Guarda no state assinado o ID da conta SN7 que iniciou a conexão.
    # Assim, ao adicionar a Kick depois da Twitch/YouTube, o callback sabe
    # exatamente qual canal deve receber a nova integração sem depender do
    # cookie da sessão.
    previous_id = get_session_broadcaster_id(validate=False)
    state = _make_kick_oauth_state(previous_id)
    verifier = _kick_pkce_verifier(state)
    challenge = _pkce_challenge(verifier)
    # Mantemos as chaves antigas apenas para compatibilidade com sessões já
    # abertas; o callback novo não depende delas.
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
    state = str(params.get("state", "") or "")
    state_data = _read_kick_oauth_state(state)
    # Limpa os valores legados da sessão, mas não depende deles para validar
    # o retorno. Isso elimina o erro intermitente de "state inválido" ao
    # conectar Kick depois de Twitch/YouTube.
    session.pop("kick_oauth_state", None)
    session.pop("kick_code_verifier", None)
    session.pop("kick_oauth_next", None)
    if not state_data:
        return jsonify({"ok": False, "error": "OAuth state inválido ou expirado."}), 400
    verifier = _kick_pkce_verifier(state)
    try:
        token_data = _exchange_code(params.get("code", ""), verifier)
        user = _kick_user(token_data["access_token"])
        kick_user_id = int(user.get("user_id"))
        existing_profile_id = state_data.get("broadcaster_id")
        if existing_profile_id is None:
            existing_profile_id = get_session_broadcaster_id(validate=False)
        profile_id = int(existing_profile_id) if existing_profile_id is not None else kick_user_id

        _save_connection(user, token_data, profile_id=profile_id)

        # Adicionar Kick nunca troca o perfil SN7 que já estava autenticado.
        session["sn7_broadcaster_id"] = profile_id
        session["kick_broadcaster_id"] = kick_user_id
        avatar = _resolve_profile_picture(
            token_data.get("access_token"),
            kick_user_id,
            user=user,
        )
        if avatar:
            _save_profile_picture(profile_id, avatar)
        session["kick_profile"] = {
            "id": int(profile_id),
            "kick_user_id": kick_user_id,
            "username": str(user.get("username") or user.get("slug") or user.get("channel_slug") or user.get("name") or user.get("display_name") or "Kick").strip(),
            "profile_picture_url": avatar,
        }
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
        result = _subscribe_chat(conn_data["access_token"], int(conn_data["broadcaster_user_id"]))
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
        # Executor reutilizável: evita criar uma nova thread para cada mensagem
        # e mantém o webhook livre para receber o próximo evento imediatamente.
        # Uma fila dedicada por canal preserva a ordem das mensagens e
        # evita corrida entre comandos/minigames do mesmo broadcaster.
        bid = (
            payload.get("broadcaster", {}).get("user_id")
            or payload.get("broadcaster_user_id")
            or payload.get("channel_id")
            or payload.get("user", {}).get("id")
        )
        if bid is None:
            # Mantém o comportamento anterior como fallback para eventos
            # sem identificador de canal.
            _chat_executor(0).submit(_process_webhook, payload, event_type)
        else:
            _chat_executor(bid).submit(_process_webhook, payload, event_type)
        return jsonify({"ok": True})
    except Exception as exc:
        print(f"[KICK-WEBHOOK] erro aceitando evento: {exc}", flush=True)
        return jsonify({"ok": False, "error": "falha interna"}), 500
