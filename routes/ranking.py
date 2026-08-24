from flask import Blueprint, jsonify, request
from core.database import get_conn
from core.services import get_channel, get_rank

ranking_bp = Blueprint("ranking", __name__)


@ranking_bp.get("/<int:broadcaster_id>")
def ranking(broadcaster_id):
    platform = str(request.args.get("platform") or "kick").strip().lower()
    if platform not in {"kick", "twitch", "youtube"}:
        return jsonify({"ok": False, "error": "Plataforma inválida."}), 400
    channel = get_channel(broadcaster_id)
    limit = max(1, min(int(request.args.get("limit", channel["rank_limit"])), 50))
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT username, points
                  FROM players
                 WHERE broadcaster_user_id=%s
                   AND platform=%s
                   AND points>0
                 ORDER BY points DESC, username ASC
                 LIMIT %s
                """,
                (broadcaster_id, platform, limit),
            )
            rows = [
                {"position": i + 1, "username": r[0], "points": r[1]}
                for i, r in enumerate(cur.fetchall())
            ]
    finally:
        conn.close()

    return jsonify({
        "ok": True,
        "title": channel["rank_title"],
        "currency": channel["currency_name"],
        "emoji": channel["currency_emoji"],
        "ranking": rows
    })


@ranking_bp.get("/<int:broadcaster_id>/user")
def user_rank(broadcaster_id):
    platform = str(request.args.get("platform") or "kick").strip().lower()
    if platform not in {"kick", "twitch", "youtube"}:
        return jsonify({"ok": False, "error": "Plataforma inválida."}), 400
    username = request.args.get("username", "").strip().lstrip("@")
    if not username:
        return jsonify({"ok": False, "error": "username obrigatório"}), 400

    rank = get_rank(broadcaster_id, username, platform)
    return jsonify({
        "ok": True,
        "username": username,
        "rank": rank,
        "ranked": rank is not None,
        "platform": platform,
    })


@ranking_bp.post("/<int:broadcaster_id>/reset")
def reset_ranking(broadcaster_id):
    try:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE players SET points=0, updated_at=NOW() WHERE broadcaster_user_id=%s",
                    (broadcaster_id,),
                )
                affected = cur.rowcount
            conn.commit()
        finally:
            conn.close()
        return jsonify({"ok": True, "reset_users": affected})
    except Exception as exc:
        print(f"[RANKING] RESET erro: {exc}", flush=True)
        return jsonify({"ok": False, "error": str(exc)}), 500
