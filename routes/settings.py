from flask import Blueprint, jsonify, request
from core.services import get_channel, ensure_channel
from core.database import get_conn

settings_bp = Blueprint("settings", __name__)

@settings_bp.get("/<int:broadcaster_id>")
def get_settings(broadcaster_id):
    return jsonify({"ok": True, "settings": get_channel(broadcaster_id)})

@settings_bp.put("/<int:broadcaster_id>")
def update_settings(broadcaster_id):
    data = request.get_json(silent=True) or {}
    ensure_channel(broadcaster_id)
    allowed = {"currency_name","currency_command","currency_emoji",
               "rank_title","rank_limit","duel_win_points","duel_loss_points"}
    values = {k: data[k] for k in allowed if k in data}
    if "currency_command" in values and not str(values["currency_command"]).startswith("!"):
        return jsonify({"ok": False, "error": "O comando deve começar com !"}), 400
    if not values:
        return jsonify({"ok": False, "error": "nenhuma alteração"}), 400

    sets = ", ".join(f"{k}=%s" for k in values)
    params = list(values.values()) + [broadcaster_id]
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE channels SET {sets}, updated_at=NOW() WHERE broadcaster_user_id=%s", params)
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True, "settings": get_channel(broadcaster_id)})
