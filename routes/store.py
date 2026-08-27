import base64
import re
from flask import Blueprint, jsonify, request, session
from core.auth import get_session_broadcaster_id, require_session_broadcaster
from core.database import get_conn

store_bp = Blueprint("store", __name__)


def _viewer():
    raw = session.get("sn7_store_viewer")
    if not isinstance(raw, dict):
        return None
    try:
        uid = int(raw.get("kick_user_id"))
    except (TypeError, ValueError):
        return None
    return {"kick_user_id": uid, "username": str(raw.get("username") or "Kick"),
            "profile_picture_url": str(raw.get("profile_picture_url") or "")}


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
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id,username,points
                  FROM players
                 WHERE broadcaster_user_id=%s AND platform='kick' AND kick_user_id=%s
                 LIMIT 1
            """, (int(bid), int(viewer["kick_user_id"])))
            row = cur.fetchone()
            return ({"player_id": int(row[0]), "username": str(row[1] or viewer["username"]), "points": int(row[2] or 0)}
                    if row else {"player_id": None, "username": viewer["username"], "points": 0})
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
    if not viewer: return jsonify({"ok":False,"error":"Faça login com sua conta Kick para resgatar."}),401
    bid=channel["broadcaster_user_id"]
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
                           WHERE broadcaster_user_id=%s AND platform='kick' AND kick_user_id=%s
                           FOR UPDATE""",(int(bid),int(viewer["kick_user_id"])))
            wallet=cur.fetchone()
            if not wallet:
                cur.execute("""INSERT INTO players(broadcaster_user_id,platform,kick_user_id,username)
                               VALUES(%s,'kick',%s,%s) RETURNING id,username,points FOR UPDATE""",(int(bid),int(viewer["kick_user_id"]),viewer["username"]))
                wallet=cur.fetchone()
            points=int(wallet[2] or 0)
            if points<price: return jsonify({"ok":False,"error":f"Você precisa de {price} pontos. Saldo: {points}."}),409
            cur.execute("UPDATE players SET points=points-%s,updated_at=NOW() WHERE id=%s RETURNING points",(price,int(wallet[0])))
            new_points=int(cur.fetchone()[0])
            new_stock=stock
            if stock is not None:
                cur.execute("UPDATE store_items SET stock=stock-1,updated_at=NOW() WHERE id=%s RETURNING stock",(int(item_id),)); new_stock=int(cur.fetchone()[0])
            cur.execute("""INSERT INTO store_redemptions(broadcaster_user_id,item_id,viewer_kick_user_id,viewer_username,price,status)
                           VALUES(%s,%s,%s,%s,%s,'queued') RETURNING id""",(int(bid),int(item_id),int(viewer["kick_user_id"]),viewer["username"],price))
            redemption_id=int(cur.fetchone()[0])
            if item[1]=='audio':
                cur.execute("""INSERT INTO store_audio_queue(broadcaster_user_id,redemption_id,item_id,viewer_kick_user_id,viewer_username,audio_url)
                               VALUES(%s,%s,%s,%s,%s,%s)""",(int(bid),redemption_id,int(item_id),int(viewer["kick_user_id"]),viewer["username"],item[5]))
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
            cur.execute("""SELECT r.id,r.item_id,i.name,i.item_type,r.viewer_kick_user_id,r.viewer_username,r.price,r.status,r.created_at
                            FROM store_redemptions r JOIN store_items i ON i.id=r.item_id
                           WHERE r.broadcaster_user_id=%s ORDER BY r.id DESC LIMIT %s""",(int(broadcaster_id),limit))
            rows=cur.fetchall()
    finally: conn.close()
    return jsonify({"ok":True,"redemptions":[{"id":int(r[0]),"item_id":int(r[1]),"name":r[2],"item_type":r[3],"viewer_kick_user_id":int(r[4]),"viewer_username":r[5],"price":int(r[6]),"status":r[7],"created_at":r[8].isoformat()} for r in rows]})


@store_bp.get("/<int:broadcaster_id>/audio/next")
def audio_next(broadcaster_id):
    require_session_broadcaster(broadcaster_id)
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
    require_session_broadcaster(broadcaster_id)
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
