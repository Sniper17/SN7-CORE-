from flask import Blueprint, jsonify, request
from core.database import get_conn
from core.services import get_channel, get_rank
from core.auth import require_session_broadcaster
from core.cache import get_cached_ranking, set_cached_ranking, forget_rankings

ranking_bp = Blueprint("ranking", __name__)


@ranking_bp.get("/<int:broadcaster_id>")
def ranking(broadcaster_id):
    platform = str(request.args.get("platform") or "kick").strip().lower()
    if platform not in {"kick", "twitch", "youtube"}:
        return jsonify({"ok": False, "error": "Plataforma inválida."}), 400
    channel = get_channel(broadcaster_id)
    limit = max(1, min(int(request.args.get("limit", channel["rank_limit"])), 50))
    cached = get_cached_ranking(broadcaster_id, platform, limit)
    if cached is not None:
        return jsonify(cached)
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

    payload = {
        "ok": True,
        "title": channel["rank_title"],
        "currency": channel["currency_name"],
        "emoji": channel["currency_emoji"],
        "ranking": rows
    }
    set_cached_ranking(broadcaster_id, payload, platform, limit)
    return jsonify(payload)


@ranking_bp.get("/<int:broadcaster_id>/all")
def all_rankings(broadcaster_id):
    """Retorna os três rankings em uma única consulta para o painel."""
    channel = get_channel(broadcaster_id)
    limit = max(1, min(int(request.args.get("limit", channel["rank_limit"])), 50))
    cached = get_cached_ranking(broadcaster_id, "all", limit, all_platforms=True)
    if cached is not None:
        return jsonify(cached)
    platforms = ("kick", "twitch", "youtube")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT platform, username, points
                  FROM players
                 WHERE broadcaster_user_id=%s
                   AND platform IN (%s,%s,%s)
                   AND points>0
                 ORDER BY platform ASC, points DESC, username ASC
                """,
                (int(broadcaster_id), *platforms),
            )
            grouped = {platform: [] for platform in platforms}
            for platform, username, points in cur.fetchall():
                rows = grouped.get(str(platform).lower())
                if rows is not None and len(rows) < limit:
                    rows.append({
                        "position": len(rows) + 1,
                        "username": username,
                        "points": points,
                    })
    finally:
        conn.close()

    payload = {
        "ok": True,
        "title": channel["rank_title"],
        "currency": channel["currency_name"],
        "emoji": channel["currency_emoji"],
        "rankings": grouped,
    }
    set_cached_ranking(broadcaster_id, payload, "all", limit, all_platforms=True)
    return jsonify(payload)


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
    platform = str(request.args.get("platform") or "").strip().lower()
    if platform not in {"kick", "twitch", "youtube"}:
        return jsonify({"ok": False, "error": "Informe uma plataforma válida para o reset."}), 400
    try:
        require_session_broadcaster(broadcaster_id)
    except PermissionError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 401

    try:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE players
                       SET points=0, updated_at=NOW()
                     WHERE broadcaster_user_id=%s AND platform=%s
                    """,
                    (int(broadcaster_id), platform),
                )
                affected = cur.rowcount
            conn.commit()
        finally:
            conn.close()
        forget_rankings(broadcaster_id)
        return jsonify({"ok": True, "platform": platform, "reset_users": affected})
    except Exception as exc:
        print(f"[RANKING] RESET erro platform={platform}: {exc}", flush=True)
        return jsonify({"ok": False, "error": str(exc)}), 500
