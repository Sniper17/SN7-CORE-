from flask import Blueprint, jsonify, request

from core.minigames import get_settings, update_settings
from core.auth import require_session_broadcaster

minigames_bp = Blueprint("minigames", __name__)


def _platform(value):
    value = str(value or "kick").strip().lower()
    return value if value in {"kick", "twitch", "youtube"} else None


@minigames_bp.get("/<int:broadcaster_id>")
def get_minigames(broadcaster_id):
    platform = _platform(request.args.get("platform"))
    if not platform:
        return jsonify({"ok": False, "error": "Plataforma inválida."}), 400
    return jsonify({"ok": True, "platform": platform, "settings": get_settings(broadcaster_id, platform)})


@minigames_bp.put("/<int:broadcaster_id>")
def save_minigames(broadcaster_id):
    try:
        require_session_broadcaster(broadcaster_id)
    except PermissionError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 403
    data = request.get_json(silent=True) or {}
    platform = _platform(data.get("platform"))
    if not platform:
        return jsonify({"ok": False, "error": "Plataforma inválida."}), 400
    allowed = {
        "enabled", "slot_bankroll", "slot_bankroll_max", "slot_hourly_refill",
        "slot_min_bet", "slot_max_bet", "slot_cooldown_seconds",
    }
    values = {key: data[key] for key in allowed if key in data}
    try:
        settings = update_settings(broadcaster_id, platform, values)
    except (TypeError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        print(f"[MINIGAMES] save erro: {exc}", flush=True)
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, "platform": platform, "settings": settings})
