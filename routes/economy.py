from flask import Blueprint, jsonify, request
from core.services import get_channel, get_player, get_rank, ensure_player
from core.database import get_conn

economy_bp = Blueprint("economy", __name__)

@economy_bp.get("/<int:broadcaster_id>/balance")
def balance(broadcaster_id):
    username = request.args.get("username", "").strip().lstrip("@")
    if not username:
        return jsonify({"ok": False, "error": "username obrigatório"}), 400
    channel = get_channel(broadcaster_id)
    player = get_player(broadcaster_id, username)
    return jsonify({
        "ok": True, "username": username, "points": player["points"],
        "rank": get_rank(broadcaster_id, username),
        "currency": channel["currency_name"], "emoji": channel["currency_emoji"]
    })

@economy_bp.post("/<int:broadcaster_id>/add")
def add_points(broadcaster_id):
    data = request.get_json(silent=True) or {}
    username = str(data.get("username", "")).strip().lstrip("@")
    amount = int(data.get("amount", 0))
    if not username or amount < 0:
        return jsonify({"ok": False, "error": "dados inválidos"}), 400
    ensure_player(broadcaster_id, username)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('''UPDATE players SET points=points+%s, updated_at=NOW()
                           WHERE broadcaster_user_id=%s AND username=%s
                           RETURNING points''',
                        (amount, broadcaster_id, username))
            row = cur.fetchone()
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True, "username": username, "points": row[0]})

@economy_bp.post("/<int:broadcaster_id>/set")
def set_points(broadcaster_id):
    data = request.get_json(silent=True) or {}
    username = str(data.get("username", "")).strip().lstrip("@")
    amount = int(data.get("amount", 0))
    if not username or amount < 0:
        return jsonify({"ok": False, "error": "dados inválidos"}), 400
    ensure_player(broadcaster_id, username)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('''UPDATE players SET points=%s, updated_at=NOW()
                           WHERE broadcaster_user_id=%s AND username=%s
                           RETURNING points''',
                        (amount, broadcaster_id, username))
            row = cur.fetchone()
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True, "username": username, "points": row[0]})
