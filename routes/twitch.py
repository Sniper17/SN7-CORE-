import os, time, secrets, hashlib, hmac, base64
from urllib.parse import urlencode
import requests
from flask import Blueprint, request, redirect, jsonify, session
from core.database import get_conn
from core.auth import require_session_broadcaster, get_session_broadcaster_id
from routes.kick import _process_chat

twitch_bp = Blueprint("twitch", __name__)
TWITCH_API="https://api.twitch.tv/helix"
TWITCH_OAUTH="https://id.twitch.tv/oauth2"


def _env(k, d=""): return os.environ.get(k,d).strip()
def _cfg(): return (_env("TWITCH_CLIENT_ID"), _env("TWITCH_CLIENT_SECRET"))
def _redirect(): return _env("TWITCH_REDIRECT_URI") or (_env("SN7_PUBLIC_URL","https://sn7-core.onrender.com")+"/twitch/callback")

def _conn(bid):
    c=get_conn()
    try:
        with c.cursor() as cur:
            cur.execute("SELECT broadcaster_user_id,external_user_id,username,display_name,profile_url,avatar_url,access_token,refresh_token,expires_at,scope,bot_active FROM chat_connections WHERE broadcaster_user_id=%s AND provider='twitch'",(int(bid),))
            r=cur.fetchone()
    finally: c.close()
    if not r:return None
    return dict(broadcaster_user_id=r[0],external_user_id=r[1],username=r[2],display_name=r[3],profile_url=r[4],avatar_url=r[5],access_token=r[6],refresh_token=r[7],expires_at=int(r[8] or 0),scope=r[9] or '',bot_active=bool(r[10]))

def _refresh(c):
    if not c.get('refresh_token'): return c
    cid,sec=_cfg()
    r=requests.post(f"{TWITCH_OAUTH}/token",params={'grant_type':'refresh_token','refresh_token':c['refresh_token'],'client_id':cid,'client_secret':sec},timeout=15)
    d=r.json()
    if r.status_code>=400 or not d.get('access_token'): raise RuntimeError(d.get('message') or 'Token Twitch expirado.')
    c['access_token']=d['access_token']; c['refresh_token']=d.get('refresh_token') or c['refresh_token']; c['expires_at']=int(time.time())+int(d.get('expires_in') or 14400)
    db=get_conn()
    try:
        with db.cursor() as cur:cur.execute("UPDATE chat_connections SET access_token=%s,refresh_token=%s,expires_at=%s,updated_at=NOW() WHERE broadcaster_user_id=%s AND provider='twitch'",(c['access_token'],c['refresh_token'],c['expires_at'],c['broadcaster_user_id']))
        db.commit()
    finally:db.close()
    return c

def _valid(c):
    if c and c.get('expires_at',0) <= int(time.time())+60: return _refresh(c)
    return c

def _save(bid, token, user):
    exp=int(time.time())+int(token.get('expires_in') or 0)
    c=get_conn()
    try:
        with c.cursor() as cur:
            cur.execute("""INSERT INTO chat_connections(broadcaster_user_id,provider,external_user_id,username,display_name,profile_url,avatar_url,access_token,refresh_token,expires_at,scope,updated_at) VALUES(%s,'twitch',%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW()) ON CONFLICT(broadcaster_user_id,provider) DO UPDATE SET external_user_id=EXCLUDED.external_user_id,username=EXCLUDED.username,display_name=EXCLUDED.display_name,profile_url=EXCLUDED.profile_url,avatar_url=EXCLUDED.avatar_url,access_token=EXCLUDED.access_token,refresh_token=COALESCE(EXCLUDED.refresh_token,chat_connections.refresh_token),expires_at=EXCLUDED.expires_at,scope=EXCLUDED.scope,updated_at=NOW()""",(int(bid),user.get('id',''),user.get('login',''),user.get('display_name',''),f"https://twitch.tv/{user.get('login','')}",user.get('profile_image_url',''),token.get('access_token'),token.get('refresh_token'),exp,token.get('scope','')))
        c.commit()
    finally:c.close()

def _token_exchange(code, verifier):
    cid,sec=_cfg()
    r=requests.post(f"{TWITCH_OAUTH}/token",data={'client_id':cid,'client_secret':sec,'code':code,'grant_type':'authorization_code','redirect_uri':_redirect()},timeout=15)
    d=r.json()
    if r.status_code>=400 or not d.get('access_token'): raise RuntimeError(d.get('message') or d.get('error') or 'OAuth Twitch recusado.')
    return d

def _user(token):
    cid,_=_cfg(); r=requests.get(f"{TWITCH_API}/users",headers={'Client-Id':cid,'Authorization':f'Bearer {token}'},timeout=15); d=r.json()
    if r.status_code>=400 or not d.get('data'): raise RuntimeError('Twitch não retornou o usuário autenticado.')
    return d['data'][0]

def _app_token():
    cid,sec=_cfg(); r=requests.post(f"{TWITCH_OAUTH}/token",params={'client_id':cid,'client_secret':sec,'grant_type':'client_credentials'},timeout=15); d=r.json()
    if r.status_code>=400: raise RuntimeError(d.get('message') or 'Não foi possível obter token da aplicação Twitch.')
    return d['access_token']

def _subscribe(bid, conn):
    app_token=_app_token(); cid,_=_cfg(); callback=_env('TWITCH_EVENTSUB_CALLBACK') or (_env('SN7_PUBLIC_URL','https://sn7-core.onrender.com')+'/twitch/eventsub'); secret=_env('TWITCH_EVENTSUB_SECRET')
    if not secret: raise RuntimeError('TWITCH_EVENTSUB_SECRET não configurado no Render.')
    headers={'Client-Id':cid,'Authorization':f'Bearer {app_token}'}
    existing=requests.get(f"{TWITCH_API}/eventsub/subscriptions",headers=headers,timeout=15)
    if existing.ok:
        for sub in (existing.json().get('data') or []):
            condition=sub.get('condition') or {}
            if sub.get('type')=='channel.chat.message' and condition.get('broadcaster_user_id')==str(conn['external_user_id']):
                requests.delete(f"{TWITCH_API}/eventsub/subscriptions",headers=headers,params={'id':sub.get('id')},timeout=15)
    payload={'type':'channel.chat.message','version':'1','condition':{'broadcaster_user_id':str(conn['external_user_id']),'user_id':str(conn['external_user_id'])},'transport':{'method':'webhook','callback':callback,'secret':secret}}
    r=requests.post(f"{TWITCH_API}/eventsub/subscriptions",headers={**headers,'Content-Type':'application/json'},json=payload,timeout=15)
    if r.status_code>=400: raise RuntimeError(f'Twitch EventSub HTTP {r.status_code}: {r.text[:500]}')

def _unsubscribe(bid, conn):
    try:
        app_token=_app_token(); cid,_=_cfg(); r=requests.get(f"{TWITCH_API}/eventsub/subscriptions",headers={'Client-Id':cid,'Authorization':f'Bearer {app_token}'},timeout=15)
        for sub in (r.json().get('data') or []):
            c=sub.get('condition') or {}
            if sub.get('type')=='channel.chat.message' and c.get('broadcaster_user_id')==str(conn['external_user_id']):
                requests.delete(f"{TWITCH_API}/eventsub/subscriptions",headers={'Client-Id':cid,'Authorization':f'Bearer {app_token}'},params={'id':sub['id']},timeout=15)
    except Exception: pass

@twitch_bp.get('/login')
def login():
    bid=get_session_broadcaster_id()
    if bid is None:return jsonify({'ok':False,'error':'Entre com a Kick primeiro para vincular o Twitch ao mesmo canal SN7.'}),401
    cid,sec=_cfg()
    if not cid or not sec:return jsonify({'ok':False,'error':'TWITCH_CLIENT_ID/TWITCH_CLIENT_SECRET não configurados no Render.'}),503
    state=secrets.token_urlsafe(32); session['twitch_oauth']={'state':state,'broadcaster_id':int(bid)}
    params={'client_id':cid,'redirect_uri':_redirect(),'response_type':'code','scope':'user:read:chat user:write:chat','state':state}
    return redirect(f"{TWITCH_OAUTH}/authorize?{urlencode(params)}")

@twitch_bp.get('/callback')
def callback():
    data=session.pop('twitch_oauth',None)
    if not data or request.args.get('state')!=data.get('state'):return jsonify({'ok':False,'error':'OAuth Twitch inválido ou expirado.'}),400
    try:
        require_session_broadcaster(data['broadcaster_id']); token=_token_exchange(request.args.get('code',''),None); user=_user(token); _save(data['broadcaster_id'],token,user); return redirect('/dashboard?profile=1&twitch_connected=1')
    except Exception as exc:return jsonify({'ok':False,'error':str(exc)}),502

@twitch_bp.get('/<int:bid>/status')
def status(bid):
    try: require_session_broadcaster(bid)
    except PermissionError as e:return jsonify({'ok':False,'error':str(e)}),403
    c=_valid(_conn(bid)); return jsonify({'ok':True,'configured':bool(_cfg()[0] and _cfg()[1]),'connected':bool(c),'active':bool(c and c['bot_active']),'user':({'id':c['external_user_id'],'username':c['username'],'display_name':c['display_name'],'avatar_url':c['avatar_url']} if c else None)})

@twitch_bp.post('/<int:bid>/bot/toggle')
def toggle(bid):
    try: require_session_broadcaster(bid)
    except PermissionError as e:return jsonify({'ok':False,'error':str(e)}),403
    c=_valid(_conn(bid))
    if not c:return jsonify({'ok':False,'error':'Conecte o Twitch primeiro.'}),403
    desired=bool((request.get_json(silent=True) or {}).get('active'))
    try:
        if desired:_subscribe(bid,c)
        else:_unsubscribe(bid,c)
        db=get_conn()
        try:
            with db.cursor() as cur:cur.execute("UPDATE chat_connections SET bot_active=%s,updated_at=NOW() WHERE broadcaster_user_id=%s AND provider='twitch'",(desired,bid))
            db.commit()
        finally:db.close()
        return jsonify({'ok':True,'active':desired})
    except Exception as exc:return jsonify({'ok':False,'error':str(exc)}),502

@twitch_bp.post('/eventsub')
def eventsub():
    secret=_env('TWITCH_EVENTSUB_SECRET')
    msg_id=request.headers.get('Twitch-Eventsub-Message-Id',''); ts=request.headers.get('Twitch-Eventsub-Message-Timestamp',''); sig=request.headers.get('Twitch-Eventsub-Message-Signature','')
    body=request.get_data(); expected='sha256='+hmac.new(secret.encode(),(msg_id+ts).encode()+body,hashlib.sha256).hexdigest() if secret else ''
    if not secret or not hmac.compare_digest(expected,sig):return ('',403)
    typ=request.headers.get('Twitch-Eventsub-Message-Type')
    payload=request.get_json(silent=True) or {}
    if typ=='webhook_callback_verification':return (payload.get('challenge',''),200,{'Content-Type':'text/plain'})
    if typ!='notification':return ('',204)
    ev=payload.get('event') or {}; bid=int(ev.get('broadcaster_user_id') or 0); c=_valid(_conn(bid))
    if not c or not c['bot_active']:return ('',204)
    sender={'user_id':ev.get('chatter_user_id'),'username':ev.get('chatter_user_login') or ev.get('chatter_user_name'),'is_moderator':bool(ev.get('badges') and any((b.get('set_id')=='moderator') for b in ev.get('badges') or [])),'is_broadcaster':ev.get('chatter_user_id')==ev.get('broadcaster_user_id')}
    norm={'broadcaster':{'user_id':bid,'username':c['username']},'sender':sender,'content':ev.get('message',{}).get('text','')}
    def send(_bid,message):
        cid,_=_cfg(); r=requests.post(f"{TWITCH_API}/chat/messages",headers={'Client-Id':cid,'Authorization':f"Bearer {c['access_token']}",'Content-Type':'application/json'},json={'broadcaster_id':c['external_user_id'],'sender_id':c['external_user_id'],'message':str(message)[:500]},timeout=10)
        if r.status_code>=400: print('[TWITCH-CHAT] send falhou',r.text[:500],flush=True)
    _process_chat(norm,send); return ('',204)
