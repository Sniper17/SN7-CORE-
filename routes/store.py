import base64
import hashlib
import hmac
import os
import re
import time
from flask import Blueprint, jsonify, request, session
from core.auth import get_session_broadcaster_id, require_session_broadcaster
from core.database import get_conn

store_bp = Blueprint("store", __name__)


def _store_signing_secret():
    return (os.environ.get("FLASK_SECRET_KEY") or os.environ.get("KICK_CLIENT_SECRET") or "sn7-store-secret").encode("utf-8")


def make_audio_player_token(broadcaster_id, ttl=30 * 24 * 60 * 60):
    payload = f"{int(broadcaster_id)}:{int(time.time()) + int(ttl)}".encode("utf-8")
    body = base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")
    sig = hmac.new(_store_signing_secret(), body.encode("ascii"), hashlib.sha256).digest()
    return body + "." + base64.urlsafe_b64encode(sig).rstrip(b"=").decode("ascii")


def validate_audio_player_token(token, broadcaster_id):
    try:
        body, sig = str(token or "").split(".", 1)
        expected = hmac.new(_store_signing_secret(), body.encode("ascii"), hashlib.sha256).digest()
        supplied = base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4))
        if not hmac.compare_digest(supplied, expected):
            return False
        payload = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode("utf-8")
        bid, exp = payload.split(":", 1)
        return int(bid) == int(broadcaster_id) and int(exp) >= int(time.time())
    except Exception:
        return False


def _audio_access_ok(broadcaster_id):
    try:
        require_session_broadcaster(broadcaster_id)
        return True
    except PermissionError:
        return validate_audio_player_token(request.args.get("token"), broadcaster_id)


def _music_duck(broadcaster_id, duck_percent=0.18):
    """Reduz temporariamente o volume da música enquanto um áudio da Loja toca.

    Retorna o volume anterior e o volume aplicado. O player restaura somente
    se o volume ainda estiver no valor aplicado, evitando sobrescrever uma
    alteração manual feita pelo streamer durante o resgate.
    """
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT volume, is_playing
                  FROM music_player_state
                 WHERE broadcaster_user_id=%s
                 FOR UPDATE
            """, (int(broadcaster_id),))
            row = cur.fetchone()
            if not row:
                return {"active": False, "original_volume": None, "ducked_volume": None}
            original = max(0, min(100, int(row[0] or 0)))
            if not bool(row[1]):
                return {"active": False, "original_volume": original, "ducked_volume": original}
            ducked = max(0, min(100, int(round(original * float(duck_percent)))))
            if ducked >= original and original > 0:
                ducked = max(0, original - 1)
            cur.execute("""
                UPDATE music_player_state
                   SET volume=%s, updated_at=NOW()
                 WHERE broadcaster_user_id=%s
            """, (ducked, int(broadcaster_id)))
        conn.commit()
        return {"active": True, "original_volume": original, "ducked_volume": ducked}
    finally:
        conn.close()


def _music_restore(broadcaster_id, original_volume, ducked_volume):
    try:
        original = max(0, min(100, int(original_volume)))
        ducked = max(0, min(100, int(ducked_volume)))
    except (TypeError, ValueError):
        return False
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE music_player_state
                   SET volume=%s, updated_at=NOW()
                 WHERE broadcaster_user_id=%s
                   AND volume=%s
            """, (original, int(broadcaster_id), ducked))
            changed = cur.rowcount
        conn.commit()
        return bool(changed)
    finally:
        conn.close()


def _viewer():
    raw = session.get("sn7_store_viewer")
    if not isinstance(raw, dict):
        return None
    try:
        uid = int(raw.get("kick_user_id")) if raw.get("kick_user_id") not in (None, "") else None
    except (TypeError, ValueError):
        uid = None
    platform = str(raw.get("platform") or "kick").strip().lower()
    if platform not in {"kick", "twitch", "youtube"}:
        platform = "kick"
    return {
        "kick_user_id": uid,
        "external_user_id": str(raw.get("external_user_id") or uid),
        "platform": platform,
        "username": str(raw.get("username") or platform.title()),
        "profile_picture_url": str(raw.get("profile_picture_url") or ""),
    }


def _resolve_channel(target):
    value = str(target or "").strip()
    if not value:
        return None
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if value.isdigit():
                cur.execute("""
                    SELECT broadcaster_user_id, username
                      FROM channels WHERE broadcaster_user_id=%s
                    LIMIT 1
                """, (int(value),))
            else:
                cur.execute("""
                    SELECT broadcaster_user_id, username
                      FROM channels WHERE LOWER(username)=LOWER(%s)
                    LIMIT 1
                """, (value,))
            row = cur.fetchone()
            if row:
                return {"broadcaster_user_id": int(row[0]), "username": str(row[1] or value)}
            if not value.isdigit():
                cur.execute("""
                    SELECT COALESCE(sn7_profile_id,broadcaster_user_id), username
                      FROM kick_connections
                     WHERE LOWER(username)=LOWER(%s)
                    LIMIT 1
                """, (value,))
                row = cur.fetchone()
                if row:
                    return {"broadcaster_user_id": int(row[0]), "username": str(row[1] or value)}
    finally:
        conn.close()
    return None


def _channel_items(bid, include_inactive=False):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id,item_type,name,description,image_url,audio_url,price,stock,active
                  FROM store_items
                 WHERE broadcaster_user_id=%s
                   AND (%s OR active=TRUE)
                 ORDER BY id DESC
            """, (int(bid), bool(include_inactive)))
            rows = cur.fetchall()
    finally:
        conn.close()
    return [{"id": int(r[0]), "item_type": r[1], "name": r[2], "description": r[3],
             "image_url": r[4], "audio_url": r[5], "price": int(r[6]),
             "stock": (int(r[7]) if r[7] is not None else None), "active": bool(r[8])} for r in rows]


def _wallet(bid, viewer):
    platform = str(viewer.get("platform") or "kick").lower()
    username = str(viewer.get("username") or "").strip()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id,username,points
                  FROM players
                 WHERE broadcaster_user_id=%s
                   AND platform=%s
                   AND LOWER(username)=LOWER(%s)
                 ORDER BY id DESC
                 LIMIT 1
            """, (int(bid), platform, username))
            row = cur.fetchone()
            return (
                {"player_id": int(row[0]), "username": str(row[1] or username), "points": int(row[2] or 0)}
                if row else {"player_id": None, "username": username, "points": 0}
            )
    finally:
        conn.close()


def _target_for_item(bid, item_id):
    conn=get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id,item_type,name,description,image_url,audio_url,price,stock,active FROM store_items WHERE id=%s AND broadcaster_user_id=%s FOR UPDATE", (int(item_id),int(bid)))
            r=cur.fetchone()
            if not r: return None
            return r
    finally:
        conn.close()


@store_bp.get("/viewer")
def viewer_status():
    viewer = _viewer()
    return jsonify({"ok": True, "authenticated": bool(viewer), "viewer": viewer})


@store_bp.post("/viewer/logout")
def viewer_logout():
    session.pop("sn7_store_viewer", None)
    session.modified = True
    return jsonify({"ok": True, "logged_out": True})


@store_bp.get("/<target>/public")
def public_store(target):
    channel = _resolve_channel(target)
    if not channel:
        return jsonify({"ok": False, "error": "Loja/canal não encontrado."}), 404
    viewer = _viewer()
    wallet = _wallet(channel["broadcaster_user_id"], viewer) if viewer else None
    return jsonify({"ok": True, "channel": channel, "viewer": viewer,
                    "wallet": wallet, "items": _channel_items(channel["broadcaster_user_id"])})


@store_bp.get("/<int:broadcaster_id>/admin")
def admin_store(broadcaster_id):
    require_session_broadcaster(broadcaster_id)
    return jsonify({"ok": True, "channel": {"broadcaster_user_id": int(broadcaster_id)},
                    "items": _channel_items(broadcaster_id, True)})


@store_bp.post("/<int:broadcaster_id>/items")
def create_item(broadcaster_id):
    require_session_broadcaster(broadcaster_id)
    data=request.get_json(silent=True) or {}
    item_type=str(data.get("item_type") or "reward").strip().lower()
    name=str(data.get("name") or "").strip()
    description=str(data.get("description") or "").strip()
    image_url=str(data.get("image_url") or "").strip()
    audio_url=str(data.get("audio_url") or "").strip()
    try: price=int(data.get("price"))
    except (TypeError,ValueError): return jsonify({"ok":False,"error":"Preço inválido."}),400
    stock=data.get("stock")
    if stock in (None,"",-1): stock=None
    else:
        try: stock=int(stock)
        except (TypeError,ValueError): return jsonify({"ok":False,"error":"Estoque inválido."}),400
        if stock < 0: return jsonify({"ok":False,"error":"Estoque inválido."}),400
    if item_type not in {"reward","audio"} or not name or len(name)>80 or len(description)>300 or price<=0:
        return jsonify({"ok":False,"error":"Dados do item inválidos."}),400
    if item_type=="audio" and not audio_url:
        return jsonify({"ok":False,"error":"Informe a URL do áudio para uma recompensa de áudio."}),400
    if image_url.startswith("data:") and len(image_url)>2_500_000:
        return jsonify({"ok":False,"error":"Imagem muito grande."}),400
    if audio_url and len(audio_url)>7_000_000: return jsonify({"ok":False,"error":"Áudio muito grande. O limite é 4 MB."}),400
    conn=get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO store_items
                (broadcaster_user_id,item_type,name,description,image_url,audio_url,price,stock)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id""",(int(broadcaster_id),item_type,name,description,image_url,audio_url,price,stock))
            item_id=int(cur.fetchone()[0])
        conn.commit()
    finally: conn.close()
    return jsonify({"ok":True,"item_id":item_id})


@store_bp.patch("/<int:broadcaster_id>/items/<int:item_id>")
def update_item(broadcaster_id,item_id):
    require_session_broadcaster(broadcaster_id)
    data=request.get_json(silent=True) or {}
    fields=[]; values=[]
    for key in ("name","description","image_url","audio_url","price","stock","active","item_type"):
        if key in data:
            val=data[key]
            if key=="price":
                try: val=int(val)
                except: return jsonify({"ok":False,"error":"Preço inválido."}),400
            if key=="stock" and val not in (None,""):
                try: val=int(val)
                except: return jsonify({"ok":False,"error":"Estoque inválido."}),400
            fields.append(f"{key}=%s"); values.append(val)
    if not fields: return jsonify({"ok":False,"error":"Nada para atualizar."}),400
    values += [int(item_id),int(broadcaster_id)]
    conn=get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE store_items SET {', '.join(fields)},updated_at=NOW() WHERE id=%s AND broadcaster_user_id=%s",values)
            changed=cur.rowcount
        conn.commit()
    finally: conn.close()
    return jsonify({"ok":bool(changed)})


@store_bp.delete("/<int:broadcaster_id>/items/<int:item_id>")
def delete_item(broadcaster_id,item_id):
    require_session_broadcaster(broadcaster_id)
    conn=get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM store_redemptions WHERE item_id=%s",(int(item_id),))
            if int(cur.fetchone()[0] or 0)>0:
                return jsonify({"ok":False,"error":"Este item já possui resgates. Desative-o para preservar o histórico."}),409
            cur.execute("DELETE FROM store_items WHERE id=%s AND broadcaster_user_id=%s",(int(item_id),int(broadcaster_id)))
            changed=cur.rowcount
        conn.commit()
    finally: conn.close()
    return jsonify({"ok":bool(changed)})


@store_bp.post("/<target>/redeem/<int:item_id>")
def redeem(target,item_id):
    channel=_resolve_channel(target)
    viewer=_viewer()
    if not channel: return jsonify({"ok":False,"error":"Loja/canal não encontrado."}),404
    if not viewer: return jsonify({"ok":False,"error":"Faça login com uma plataforma para resgatar."}),401
    bid=channel["broadcaster_user_id"]
    platform = str(viewer.get("platform") or "kick").lower()
    username = str(viewer.get("username") or "").strip()
    conn=get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT id,item_type,name,description,image_url,audio_url,price,stock,active
                             FROM store_items WHERE id=%s AND broadcaster_user_id=%s FOR UPDATE""",(int(item_id),int(bid)))
            item=cur.fetchone()
            if not item or not item[8]: return jsonify({"ok":False,"error":"Item indisponível."}),404
            price=int(item[6]); stock=item[7]
            if stock is not None and int(stock)<=0: return jsonify({"ok":False,"error":"Item esgotado."}),409
            cur.execute("""SELECT id,username,points FROM players
                           WHERE broadcaster_user_id=%s AND platform=%s AND LOWER(username)=LOWER(%s)
                           ORDER BY id DESC LIMIT 1
                           FOR UPDATE""",(int(bid),platform,username))
            wallet=cur.fetchone()
            if not wallet:
                kick_uid = viewer.get("kick_user_id") if platform == "kick" else None
                cur.execute("""INSERT INTO players(broadcaster_user_id,platform,kick_user_id,username)
                               VALUES(%s,%s,%s,%s) RETURNING id,username,points FOR UPDATE""",
                            (int(bid),platform,kick_uid,username))
                wallet=cur.fetchone()
            points=int(wallet[2] or 0)
            if points<price: return jsonify({"ok":False,"error":f"Você precisa de {price} pontos. Saldo: {points}."}),409
            cur.execute("UPDATE players SET points=points-%s,updated_at=NOW() WHERE id=%s RETURNING points",(price,int(wallet[0])))
            new_points=int(cur.fetchone()[0])
            new_stock=stock
            if stock is not None:
                cur.execute("UPDATE store_items SET stock=stock-1,updated_at=NOW() WHERE id=%s RETURNING stock",(int(item_id),)); new_stock=int(cur.fetchone()[0])
            cur.execute("""INSERT INTO store_redemptions
                           (broadcaster_user_id,item_id,platform,viewer_external_id,viewer_kick_user_id,viewer_username,price,status)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,'queued') RETURNING id""",
                        (int(bid),int(item_id),platform,str(viewer.get("external_user_id") or ""),
                         viewer.get("kick_user_id") if platform == "kick" else None,username,price))
            redemption_id=int(cur.fetchone()[0])
            if item[1]=='audio':
                cur.execute("""INSERT INTO store_audio_queue
                               (broadcaster_user_id,redemption_id,item_id,platform,viewer_external_id,viewer_kick_user_id,viewer_username,audio_url)
                               VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",
                            (int(bid),redemption_id,int(item_id),platform,str(viewer.get("external_user_id") or ""),
                             viewer.get("kick_user_id") if platform == "kick" else None,username,item[5]))
        conn.commit()
    finally: conn.close()
    return jsonify({"ok":True,"redemption_id":redemption_id,"points":new_points,"stock":new_stock,"item_id":int(item_id)})


@store_bp.get("/<int:broadcaster_id>/redemptions")
def redemptions(broadcaster_id):
    require_session_broadcaster(broadcaster_id)
    limit=max(1,min(int(request.args.get("limit",50)),200))
    conn=get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT r.id,r.item_id,i.name,i.item_type,r.platform,r.viewer_external_id,r.viewer_kick_user_id,r.viewer_username,r.price,r.status,r.created_at
                            FROM store_redemptions r JOIN store_items i ON i.id=r.item_id
                           WHERE r.broadcaster_user_id=%s ORDER BY r.id DESC LIMIT %s""",(int(broadcaster_id),limit))
            rows=cur.fetchall()
    finally: conn.close()
    return jsonify({"ok":True,"redemptions":[
        {"id":int(r[0]),"item_id":int(r[1]),"name":r[2],"item_type":r[3],
         "platform":str(r[4] or "kick"),"viewer_external_id":str(r[5] or ""),
         "viewer_kick_user_id":(int(r[6]) if r[6] is not None else None),
         "viewer_username":r[7],"price":int(r[8]),"status":r[9],"created_at":r[10].isoformat()}
        for r in rows
    ]})


@store_bp.get("/<int:broadcaster_id>/audio/player-token")
def audio_player_token(broadcaster_id):
    require_session_broadcaster(broadcaster_id)
    token = make_audio_player_token(broadcaster_id)
    public_url = os.environ.get("SN7_PUBLIC_URL", "https://sn7core.com").strip().rstrip("/")
    if "sn7-core.onrender.com" in public_url:
        public_url = "https://sn7core.com"
    channel = _resolve_channel(str(broadcaster_id)) or {"username": str(broadcaster_id)}
    target = str(channel.get("username") or broadcaster_id)
    from urllib.parse import quote
    player_url = f"{public_url}/loja/{quote(target, safe='')}/audio-player?token={quote(token, safe='')}"
    return jsonify({"ok": True, "token": token, "player_url": player_url, "expires_in": 30 * 24 * 60 * 60})


@store_bp.post("/<int:broadcaster_id>/audio/duck")
def audio_duck(broadcaster_id):
    if not _audio_access_ok(broadcaster_id):
        return jsonify({"ok": False, "error": "Player de áudio não autorizado."}), 401
    try:
        result = _music_duck(broadcaster_id)
        return jsonify({"ok": True, **result})
    except Exception as exc:
        print(f"[STORE-AUDIO] duck falhou: {exc}", flush=True)
        return jsonify({"ok": False, "error": "Não foi possível reduzir a música agora."}), 503


@store_bp.post("/<int:broadcaster_id>/audio/restore")
def audio_restore(broadcaster_id):
    if not _audio_access_ok(broadcaster_id):
        return jsonify({"ok": False, "error": "Player de áudio não autorizado."}), 401
    data = request.get_json(silent=True) or {}
    restored = _music_restore(broadcaster_id, data.get("original_volume"), data.get("ducked_volume"))
    return jsonify({"ok": True, "restored": restored})


@store_bp.get("/<int:broadcaster_id>/audio/next")
def audio_next(broadcaster_id):
    if not _audio_access_ok(broadcaster_id):
        return jsonify({"ok": False, "error": "Player de áudio não autorizado."}), 401
    conn=get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT q.id,q.redemption_id,q.item_id,q.viewer_username,q.audio_url,i.name
                            FROM store_audio_queue q JOIN store_items i ON i.id=q.item_id
                           WHERE q.broadcaster_user_id=%s AND q.status='queued'
                           ORDER BY q.id ASC LIMIT 1""",(int(broadcaster_id),))
            r=cur.fetchone()
    finally: conn.close()
    if not r: return jsonify({"ok":True,"audio":None})
    return jsonify({"ok":True,"audio":{"id":int(r[0]),"redemption_id":int(r[1]),"item_id":int(r[2]),"viewer_username":r[3],"audio_url":r[4],"name":r[5]}})


@store_bp.post("/<int:broadcaster_id>/audio/<int:queue_id>/complete")
def audio_complete(broadcaster_id,queue_id):
    if not _audio_access_ok(broadcaster_id):
        return jsonify({"ok": False, "error": "Player de áudio não autorizado."}), 401
    conn=get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE store_audio_queue SET status='done',finished_at=NOW() WHERE id=%s AND broadcaster_user_id=%s AND status IN ('queued','playing')",(int(queue_id),int(broadcaster_id)))
            changed=cur.rowcount
            if changed:
                cur.execute("UPDATE store_redemptions SET status='fulfilled',fulfilled_at=NOW() WHERE id=(SELECT redemption_id FROM store_audio_queue WHERE id=%s)",(int(queue_id),))
        conn.commit()
    finally: conn.close()
    return jsonify({"ok":bool(changed)})


@store_bp.get("/<int:broadcaster_id>/wallet/<int:viewer_kick_user_id>")
def admin_wallet(broadcaster_id,viewer_kick_user_id):
    require_session_broadcaster(broadcaster_id)
    conn=get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT username,points FROM players WHERE broadcaster_user_id=%s AND platform='kick' AND kick_user_id=%s LIMIT 1",(int(broadcaster_id),int(viewer_kick_user_id)))
            r=cur.fetchone()
    finally: conn.close()
    return jsonify({"ok":True,"wallet":{"username":r[0],"points":int(r[1] or 0)} if r else {"username":"","points":0}})
