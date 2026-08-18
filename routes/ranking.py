from flask import Blueprint, jsonify, request
from core.database import get_conn
from core.services import get_channel, get_rank

ranking_bp = Blueprint("ranking", __name__)

@ranking_bp.get("/<int:broadcaster_id>")
def ranking(broadcaster_id):
    channel = get_channel(broadcaster_id)
    limit = max(1, min(int(request.args.get("limit", channel["rank_limit"])), 50))
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('''SELECT username, points FROM players
                           WHERE broadcaster_user_id=%s
                           ORDER BY points DESC, username ASC LIMIT %s''',
                        (broadcaster_id, limit))
            rows = [{"position": i + 1, "username": r[0], "points": r[1]}
                    for i, r in enumerate(cur.fetchall())]
    finally:
        conn.close()
    return jsonify({"ok": True, "title": channel["rank_title"],
                    "currency": channel["currency_name"],
                    "emoji": channel["currency_emoji"], "ranking": rows})

@ranking_bp.get("/<int:broadcaster_id>/user")
def user_rank(broadcaster_id):
    username = request.args.get("username", "").strip().lstrip("@")
    if not username:
        return jsonify({"ok": False, "error": "username obrigatório"}), 400
    return jsonify({"ok": True, "username": username,
                    "rank": get_rank(broadcaster_id, username)})
