import os
import time
import threading
import secrets
from urllib.parse import urlencode

import requests
from flask import Blueprint, jsonify, request, redirect, session

from core.database import get_conn
from core.auth import require_session_broadcaster, get_session_broadcaster_id
from routes.kick import _process_chat

youtube_bp = Blueprint("youtube", __name__)
_worker_started = False

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/youtube/v3"

def _env(name, default=""):
    return os.environ.get(name, default).strip()

def _bot_credentials():
    # O bot usa credenciais próprias; mantém fallback para instalações antigas.
    return (
        _env("YOUTUBE_BOT_CLIENT_ID") or _env("YOUTUBE_CLIENT_ID"),
        _env("YOUTUBE_BOT_CLIENT_SECRET") or _env("YOUTUBE_CLIENT_SECRET"),
    )

def _redirect_uri():
    # OAuth do BOT é separado do OAuth usado pelo Music Player.
    configured = _env("YOUTUBE_BOT_REDIRECT_URI")
    if configured:
        return configured
    return f"{_env('SN7_PUBLIC_URL', 'https://sn7-core.onrender.com').rstrip('/')}/youtube/callback"

def _configured():
    cid, secret = _bot_credentials()
    return bool(cid and secret)

def _conn(bid):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT external_user_id, username, display_name, avatar_url, access_token,
                       refresh_token, expires_at, bot_active, cursor
                FROM chat_connections
                WHERE broadcaster_user_id=%s AND provider='youtube'
            """, (int(bid),))
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return {
        "external_user_id": row[0], "username": row[1], "display_name": row[2],
        "avatar_url": row[3] or "", "access_token": row[4], "refresh_token": row[5],
        "expires_at": int(row[6] or 0), "bot_active": bool(row[7]), "cursor": row[8] or ""
    }

def _refresh(conn_data, bid):
    if not conn_data or int(conn_data.get("expires_at") or 0) > int(time.time()) + 60:
        return conn_data
    refresh_token = conn_data.get("refresh_token")
    if not refresh_token or not _configured():
        return conn_data
    response = requests.post(TOKEN_URL, data={
        "client_id": _bot_credentials()[0],
        "client_secret": _bot_credentials()[1],
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }, timeout=15)
    data = response.json()
    if response.status_code >= 400 or not data.get("access_token"):
        raise RuntimeError(data.get("error_description") or "Token YouTube expirado.")
    conn_data["access_token"] = data["access_token"]
    conn_data["expires_at"] = int(time.time()) + int(data.get("expires_in") or 3600)
    db = get_conn()
    try:
        with db.cursor() as cur:
            cur.execute("""UPDATE chat_connections SET access_token=%s, expires_at=%s,
                          updated_at=NOW() WHERE broadcaster_user_id=%s AND provider='youtube'""",
                        (conn_data["access_token"], conn_data["expires_at"], int(bid)))
        db.commit()
    finally:
        db.close()
    return conn_data

def _save_connection(bid, token, profile):
    expires_at = int(time.time()) + int(token.get("expires_in") or 3600)
    channel_id = str(profile.get("id") or "")
    snippet = profile.get("snippet") or {}
    title = str(snippet.get("title") or "").strip()
    thumbnails = snippet.get("thumbnails") or {}
    avatar = str((thumbnails.get("high") or thumbnails.get("medium") or thumbnails.get("default") or {}).get("url") or "").strip()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO chat_connections
                (broadcaster_user_id, provider, external_user_id, username, display_name,
                 profile_url, avatar_url, access_token, refresh_token, expires_at, scope,
                 bot_active, cursor, updated_at)
                VALUES (%s,'youtube',%s,%s,%s,%s,%s,%s,%s,%s,%s,FALSE,'',NOW())
                ON CONFLICT (broadcaster_user_id, provider) DO UPDATE SET
                    external_user_id=EXCLUDED.external_user_id, username=EXCLUDED.username,
                    display_name=EXCLUDED.display_name, profile_url=EXCLUDED.profile_url,
                    avatar_url=EXCLUDED.avatar_url, access_token=EXCLUDED.access_token,
                    refresh_token=COALESCE(EXCLUDED.refresh_token, chat_connections.refresh_token),
                    expires_at=EXCLUDED.expires_at, scope=EXCLUDED.scope,
                    cursor='', updated_at=NOW()
            """, (int(bid), channel_id, channel_id, title,
                  f"https://www.youtube.com/channel/{channel_id}", avatar,
                  token.get("access_token"), token.get("refresh_token"), expires_at,
                  str(token.get("scope") or "")))
        conn.commit()
    finally:
        conn.close()

def _youtube_profile(access_token):
    response = requests.get(f"{API}/channels", params={"part":"snippet", "mine":"true", "maxResults":1},
                            headers={"Authorization": f"Bearer {access_token}"}, timeout=15)
    data = response.json()
    if response.status_code >= 400 or not data.get("items"):
        raise RuntimeError((data.get("error") or {}).get("message") or "YouTube não retornou o canal autenticado.")
    return data["items"][0]

def _oauth_error(message, status=400, retry_url="/perfil"):
    if "text/html" in str(request.headers.get("Accept") or ""):
        safe = (str(message).replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))
        retry = str(retry_url).replace("&", "&amp;").replace('"', "&quot;")
        html = """<!doctype html><html lang="pt-BR"><meta charset="utf-8"><title>SN7 • YouTube</title>
<style>body{margin:0;background:#0b0d12;color:#e8ebf2;font:16px system-ui;display:grid;place-items:center;min-height:100vh;padding:24px}main{max-width:520px;background:#121720;border:1px solid #2a3240;border-radius:18px;padding:24px}a{display:inline-block;margin:10px 8px 0 0;color:#fff;background:#1c2430;border:1px solid #354052;border-radius:10px;padding:10px 14px;text-decoration:none;font-weight:700}</style>
<main><h2>Conexão do YouTube não concluída</h2><p>__MESSAGE__</p><a href="__RETRY__">Tentar novamente</a><a href="/?profile=1">Voltar ao perfil</a></main></html>"""
        return html.replace("__MESSAGE__", safe).replace("__RETRY__", retry), status
    return jsonify({"ok": False, "error": message}), status

@youtube_bp.get("/login")
def login():
    bid = get_session_broadcaster_id()
    if bid is None:
        return _oauth_error("Entre com a Kick primeiro para vincular o YouTube ao mesmo canal SN7."), 401
    if not _configured():
        return _oauth_error("YOUTUBE_BOT_CLIENT_ID/YOUTUBE_BOT_CLIENT_SECRET não configurados no Render.", 503, "/?profile=1")
    state = secrets.token_urlsafe(32)
    session["youtube_bot_oauth"] = {"state": state, "broadcaster_id": int(bid), "created_at": int(time.time())}
    params = {
        "client_id": _bot_credentials()[0], "redirect_uri": _redirect_uri(),
        "response_type": "code", "scope": "https://www.googleapis.com/auth/youtube.force-ssl",
        "access_type": "offline", "include_granted_scopes": "true", "prompt": "consent", "state": state,
    }
    return redirect(f"{AUTH_URL}?{urlencode(params)}")

@youtube_bp.get("/callback")
def callback():
    state_data = session.pop("youtube_bot_oauth", None)
    state_age = int(time.time()) - int((state_data or {}).get("created_at") or 0)
    if not state_data or state_age > 600 or not secrets.compare_digest(str(request.args.get("state") or ""), str(state_data.get("state") or "")):
        return _oauth_error("A sessão do OAuth do YouTube expirou. Inicie a conexão novamente.", 400, "/youtube/login")
    if request.args.get("error"):
        return _oauth_error(request.args.get("error_description") or request.args.get("error"), 400)
    code = str(request.args.get("code") or "").strip()
    if not code:
        return _oauth_error("O YouTube não retornou o código de autorização.", 400)
    try:
        require_session_broadcaster(state_data["broadcaster_id"])
        response = requests.post(TOKEN_URL, data={
            "code": code, "client_id": _bot_credentials()[0],
            "client_secret": _bot_credentials()[1],
            "redirect_uri": _redirect_uri(), "grant_type": "authorization_code",
        }, timeout=15)
        token = response.json()
        if response.status_code >= 400 or not token.get("access_token"):
            raise RuntimeError(token.get("error_description") or token.get("error") or "OAuth YouTube recusado.")
        profile = _youtube_profile(token["access_token"])
        _save_connection(state_data["broadcaster_id"], token, profile)
        return redirect("/?profile=1&youtube_connected=1")
    except Exception as exc:
        print(f"[YOUTUBE-OAUTH] callback falhou: {exc}", flush=True)
        return _oauth_error(str(exc), 502)

@youtube_bp.get("/<int:bid>/status")
def status(bid):
    try:
        require_session_broadcaster(bid)
    except PermissionError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 403
    conn = _conn(bid)
    return jsonify({"ok": True, "configured": _configured(), "connected": bool(conn),
                    "active": bool(conn and conn["bot_active"]),
                    "token_expired": bool(conn and int(conn.get("expires_at") or 0) <= int(time.time()) + 60),
                    "user": ({"id": conn["external_user_id"], "username": conn["username"],
                              "display_name": conn["display_name"], "avatar_url": conn["avatar_url"]} if conn else None)})

def _find_live_chat(conn):
    # liveBroadcasts.list aceita exatamente um filtro entre
    # broadcastStatus, id e mine. Não combinamos mine=true
    # com broadcastStatus=active.
    headers = {"Authorization": f"Bearer {conn['access_token']}"}
    page_token = ""

    while True:
        params = {
            "part": "snippet,status",
            "mine": "true",
            "broadcastType": "all",
            "maxResults": 50,
        }
        if page_token:
            params["pageToken"] = page_token

        response = requests.get(
            f"{API}/liveBroadcasts",
            params=params,
            headers=headers,
            timeout=15,
        )
        data = response.json()

        if response.status_code >= 400:
            error = data.get("error") or {}
            raise RuntimeError(
                error.get("message") or "YouTube recusou a consulta."
            )

        for item in data.get("items") or []:
            status = (item.get("status") or {}).get("lifeCycleStatus")
            if status != "live":
                continue

            chat_id = (item.get("snippet") or {}).get("liveChatId")
            if chat_id:
                return chat_id

        page_token = data.get("nextPageToken") or ""
        if not page_token:
            return None

def _send(conn, text):
    response = requests.post(f"{API}/liveChat/messages", params={"part":"snippet"},
                              headers={"Authorization": f"Bearer {conn['access_token']}", "Content-Type":"application/json"},
                              json={"snippet":{"liveChatId":conn["_chat_id"],"type":"textMessageEvent","textMessageDetails":{"messageText":str(text)[:500]}}}, timeout=15)
    if response.status_code >= 400:
        print("[YOUTUBE-CHAT] send falhou", response.text[:500], flush=True)

def _save_cursor(bid, cursor):
    db = get_conn()
    try:
        with db.cursor() as cur:
            cur.execute("UPDATE chat_connections SET cursor=%s, updated_at=NOW() WHERE broadcaster_user_id=%s AND provider='youtube'", (cursor or '', int(bid)))
        db.commit()
    finally:
        db.close()

def _poll_once(bid, conn):
    chat_id = _find_live_chat(conn)
    if not chat_id:
        return 5
    conn["_chat_id"] = chat_id
    params = {"liveChatId":chat_id,"part":"snippet,authorDetails","maxResults":200}
    if conn.get("cursor"):
        params["pageToken"] = conn["cursor"]
    response = requests.get(f"{API}/liveChat/messages", params=params, headers={"Authorization": f"Bearer {conn['access_token']}"}, timeout=15)
    data = response.json()
    if response.status_code >= 400:
        raise RuntimeError((data.get("error") or {}).get("message") or "YouTube chat indisponível.")
    for item in data.get("items") or []:
        snippet = item.get("snippet") or {}; author = item.get("authorDetails") or {}
        norm = {"broadcaster":{"user_id":bid,"username":conn["username"]},
                "sender":{"user_id":author.get("channelId"),"username":author.get("displayName"),
                           "is_moderator":bool(author.get("isChatModerator")),"is_broadcaster":bool(author.get("isChatOwner"))},
                "content":snippet.get("displayMessage") or snippet.get("textMessageDetails",{}).get("messageText","")}
        _process_chat(norm, lambda _bid, msg: _send(conn, msg))
    token = data.get("nextPageToken")
    if token:
        _save_cursor(bid, token)
        conn["cursor"] = token
    return max(1, int((data.get("pollingIntervalMillis") or 5000) / 1000))

def _worker():
    while True:
        delay = 5
        try:
            db = get_conn()
            try:
                with db.cursor() as cur:
                    cur.execute("SELECT broadcaster_user_id FROM chat_connections WHERE provider='youtube' AND bot_active=TRUE")
                    bids = [r[0] for r in cur.fetchall()]
            finally:
                db.close()
            for bid in bids:
                lock = get_conn(); acquired = False
                try:
                    with lock.cursor() as cur:
                        cur.execute("SELECT pg_try_advisory_lock(hashtextextended(%s,0))", (f"sn7_youtube_{bid}",))
                        acquired = bool(cur.fetchone()[0])
                    if not acquired:
                        continue
                    conn = _refresh(_conn(bid), bid)
                    if conn:
                        try:
                            delay = min(delay, _poll_once(bid, conn))
                        except Exception as exc:
                            print(f"[YOUTUBE-CHAT] {bid}: {exc}", flush=True)
                    with lock.cursor() as cur:
                        cur.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", (f"sn7_youtube_{bid}",))
                finally:
                    lock.close()
        except Exception as exc:
            print("[YOUTUBE-CHAT] worker", exc, flush=True)
        time.sleep(max(1, delay))

def _start_worker():
    global _worker_started
    if _worker_started:
        return
    _worker_started = True
    threading.Thread(target=_worker, name="sn7-youtube-chat", daemon=True).start()

@youtube_bp.post("/<int:bid>/bot/toggle")
def toggle(bid):
    try:
        require_session_broadcaster(bid)
    except PermissionError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 403
    conn = _refresh(_conn(bid), bid)
    if not conn:
        return jsonify({"ok": False, "error": "Conecte o YouTube primeiro."}), 403
    desired = bool((request.get_json(silent=True) or {}).get("active"))
    try:
        if desired and not _find_live_chat(conn):
            return jsonify({"ok": False, "error": "O YouTube precisa estar ao vivo para ativar o bot."}), 409
        db = get_conn()
        try:
            with db.cursor() as cur:
                cur.execute("UPDATE chat_connections SET bot_active=%s, updated_at=NOW() WHERE broadcaster_user_id=%s AND provider='youtube'", (desired, int(bid)))
            db.commit()
        finally:
            db.close()
        return jsonify({"ok": True, "active": desired})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502

if os.environ.get("DATABASE_URL"):
    _start_worker()
