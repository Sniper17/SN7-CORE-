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
from core.command_system import find_command, list_commands


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


def _render_response(template,values):
 text=str(template or '')
 for key,value in values.items():text=text.replace('$('+key+')',str(value))
 return text
def _format_balance(bid,user):
 ch=get_channel(bid);p=get_player(bid,user);return {'user':user,'points':int(p['points']),'currency':ch['currency_name'],'emoji':ch['currency_emoji'],'rank':get_rank(bid,user)}
def _format_ranking(bid):
 ch=get_channel(bid);limit=max(1,min(int(ch['rank_limit']),10));c=get_conn()
 try:
  with c.cursor() as x:x.execute('SELECT username,points FROM players WHERE broadcaster_user_id=%s AND points>0 ORDER BY points DESC,username ASC LIMIT %s',(int(bid),limit));rows=x.fetchall()
 finally:c.close()
 if not rows:return f"🏆 Ranking: ninguém possui {ch['currency_name']} ainda."
 return '🏆 Ranking: '+' • '.join(f'{i}. {n} {p}' for i,(n,p) in enumerate(rows,1))
def _commands_text(bid):
 a=[x for x in list_commands(bid) if x['category']=='custom' and x['enabled']];return '📜 Nenhum comando personalizado configurado.' if not a else '📜 Comandos: '+' '.join(x['command'] for x in a[:25])
def _process_chat(payload):
 broadcaster=payload.get('broadcaster') or {};sender=payload.get('sender') or {}
 try:bid=int(broadcaster.get('user_id'));uid=int(sender.get('user_id')) if sender.get('user_id') else None
 except (TypeError,ValueError):return
 user=str(sender.get('username') or sender.get('slug') or '').strip();content=str(payload.get('content') or '').strip()
 if not bid or not user or not content.startswith('!'):return
 ensure_channel(bid,str(broadcaster.get('username') or broadcaster.get('slug') or ''));ensure_player(bid,user,uid)
 pieces=content.split();cmd=pieces[0].lower();args=pieces[1:]
 try:
  cfg=find_command(bid,cmd)
  if not cfg or not cfg['enabled']:return
  ch=get_channel(bid);key=cfg['command_key'];currency=str(ch['currency_name']);emoji=str(ch['currency_emoji'])
  if key=='points':_send_chat(bid,_render_response(cfg['response'],_format_balance(bid,user)));return
  if key=='ranking':_send_chat(bid,_render_response(cfg['response'],{'ranking':_format_ranking(bid)}));return
  if key=='cmds':_send_chat(bid,_render_response(cfg['response'],{'commands':_commands_text(bid)}));return
  if key=='duel':
   if not args:return _send_chat(bid,_render_response(cfg['response'],{'duel_result':'⚔️ Use !duelo @usuário'}))
   defender=args[0].lstrip('@').strip()
   if not defender or defender.lower()==user.lower():return _send_chat(bid,_render_response(cfg['response'],{'duel_result':'⚔️ Você não pode duelar consigo mesmo.'}))
   ensure_player(bid,defender);import random;winner=random.choice([user,defender]);loser=defender if winner==user else user;win=int(ch['duel_win_points']);loss=int(ch['duel_loss_points']);c=get_conn()
   try:
    with c.cursor() as x:
     x.execute('UPDATE players SET points=points+%s WHERE broadcaster_user_id=%s AND username=%s',(win,bid,winner));x.execute('UPDATE players SET points=GREATEST(0,points-%s) WHERE broadcaster_user_id=%s AND username=%s',(loss,bid,loser));x.execute('INSERT INTO duel_events(broadcaster_user_id,attacker,defender,winner,winner_points_delta,loser_points_delta) VALUES(%s,%s,%s,%s,%s,%s)',(bid,user,defender,winner,win,-loss))
    c.commit()
   finally:c.close()
   result=f'⚔️ {user} atacou primeiro! 💥 {winner} venceu o duelo! 🏆 +{win} {currency} para {winner}. 💀 {loser} perdeu {loss}.';_send_chat(bid,_render_response(cfg['response'],{'duel_result':result,'attacker':user,'defender':defender,'winner':winner,'loser':loser,'win':win,'loss':loss,'currency':currency,'emoji':emoji}));return
  ismod=bool(sender.get('is_moderator') or sender.get('is_broadcaster') or sender.get('is_owner') or str(sender.get('user_id') or '')==str(bid))
  if key in {'addcmd','addpoint','settpoint','delcmd'} and not ismod:return _send_chat(bid,'⛔ Apenas streamer/mod pode usar este comando.')
  if key=='addcmd':
   if len(args)<2:return _send_chat(bid,'Use !addcmd !comando resposta')
   custom=args[0].lower();custom=custom if custom.startswith('!') else '!'+custom;resp=' '.join(args[1:]);allc=list_commands(bid);used={x['command'] for x in allc}|{a for x in allc for a in x['aliases']}
   if custom in used:return _send_chat(bid,'⛔ Essa palavra de ativação já está em uso.')
   c=get_conn()
   try:
    with c.cursor() as x:x.execute("INSERT INTO command_configs(broadcaster_user_id,command_key,command,description,response,enabled,category,is_system) VALUES(%s,%s,%s,%s,%s,TRUE,'custom',FALSE)",(bid,'custom:'+custom,custom,'Comando personalizado desta live.',resp))
    c.commit()
   finally:c.close()
   _send_chat(bid,_render_response(cfg['response'],{'command':custom}));return
  if key in {'addpoint','settpoint'}:
   if len(args)<2:return _send_chat(bid,f'Use {cfg["command"]} @usuário quantidade')
   target=args[0].lstrip('@').strip()
   try:amount=int(args[1]);amount=max(0,amount) if key=='settpoint' else amount
   except ValueError:return _send_chat(bid,'❌ Quantidade inválida.')
   if key=='addpoint' and amount<=0:return _send_chat(bid,'❌ A quantidade precisa ser maior que 0.')
   ensure_player(bid,target);c=get_conn()
   try:
    with c.cursor() as x:
     x.execute('UPDATE players SET points='+('points+%s' if key=='addpoint' else '%s')+' WHERE broadcaster_user_id=%s AND username=%s RETURNING points',(amount,bid,target));row=x.fetchone()
    c.commit()
   finally:c.close()
   new=int(row[0]) if row else amount;_send_chat(bid,_render_response(cfg['response'],{'target':target,'amount':amount,'new_points':new,'currency':currency,'emoji':emoji}));return
  if key=='delcmd':
   if not args:return _send_chat(bid,'Use !delcmd !comando')
   target=find_command(bid,args[0].lower())
   if not target or target['is_system']:return _send_chat(bid,'❌ Esse comando personalizado não existe.')
   c=get_conn()
   try:
    with c.cursor() as x:x.execute('DELETE FROM command_configs WHERE broadcaster_user_id=%s AND command_key=%s AND is_system=FALSE',(bid,target['command_key']));deleted=x.rowcount>0
    c.commit()
   finally:c.close()
   _send_chat(bid,_render_response(cfg['response'],{'command':args[0]}) if deleted else '❌ Comando não existe.');return
  if not cfg['is_system']:_send_chat(bid,_render_response(cfg['response'],{'user':user}))
 except Exception as exc:print(f'[KICK-CHAT] erro processando {content!r}: {exc}',flush=True)

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
