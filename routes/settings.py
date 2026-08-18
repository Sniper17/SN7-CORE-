from flask import Blueprint, jsonify, request
from core.services import get_channel, ensure_channel
from core.database import get_conn

settings_bp = Blueprint("settings", __name__)

DEFAULT_SETTINGS = {
    "broadcaster_user_id": 1,
    "username": "",
    "currency_name": "Placos",
    "currency_command": "!placos",
    "currency_emoji": "🪙",
    "rank_title": "Ranking",
    "rank_limit": 5,
    "duel_win_points": 10,
    "duel_loss_points": 3,
}

@settings_bp.get("/<int:broadcaster_id>")
def get_settings(broadcaster_id):
    try:
        return jsonify({"ok": True, "settings": get_channel(broadcaster_id), "demo": False})
    except RuntimeError as exc:
        if "DATABASE_URL" in str(exc):
            demo = dict(DEFAULT_SETTINGS)
            demo["broadcaster_user_id"] = broadcaster_id
            return jsonify({"ok": True, "settings": demo, "demo": True})
        raise

@settings_bp.put("/<int:broadcaster_id>")
def update_settings(broadcaster_id):
    data = request.get_json(silent=True) or {}
    allowed = {"currency_name", "currency_command", "currency_emoji",
               "rank_title", "rank_limit", "duel_win_points", "duel_loss_points"}
    values = {k: data[k] for k in allowed if k in data}

    if "currency_command" in values and not str(values["currency_command"]).startswith("!"):
        return jsonify({"ok": False, "error": "O comando deve começar com !"}), 400
    if not values:
        return jsonify({"ok": False, "error": "nenhuma alteração"}), 400

    try:
        ensure_channel(broadcaster_id)
        sets = ", ".join(f"{k}=%s" for k in values)
        params = list(values.values()) + [broadcaster_id]
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE channels SET {sets}, updated_at=NOW() "
                    "WHERE broadcaster_user_id=%s", params
                )
            conn.commit()
        finally:
            conn.close()
        return jsonify({"ok": True, "settings": get_channel(broadcaster_id), "demo": False})
    except RuntimeError as exc:
        if "DATABASE_URL" in str(exc):
            return jsonify({
                "ok": False,
                "demo": True,
                "error": "Modo demonstração: conecte um banco PostgreSQL no Render para salvar as alterações."
            }), 503
        raise
