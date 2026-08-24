from flask import Blueprint, jsonify, request

from core.minigames import get_settings, update_settings, update_minigame_enabled
from core.command_system import set_minigame_commands_enabled, get_minigame_command_status
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
    return jsonify({"ok": True, "platform": platform, "settings": get_settings(broadcaster_id, platform), "command_status": get_minigame_command_status(broadcaster_id)})


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
    game = str(data.get("game") or "").strip().lower()
    if game:
        if game not in {"slots", "bets", "coinflip", "polls", "quiz", "race", "target", "secret", "survival", "steal", "vault", "jackpot"}:
            return jsonify({"ok": False, "error": "Mini Game inválido."}), 400
        try:
            settings = update_minigame_enabled(broadcaster_id, platform, game, bool(data.get("game_enabled")))
            set_minigame_commands_enabled(
                broadcaster_id,
                game,
                bool(settings.get("enabled", True)) and bool(settings.get(f"{game}_enabled", True)),
            )
        except (TypeError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except Exception as exc:
            print(f"[MINIGAMES] toggle erro: {exc}", flush=True)
            return jsonify({"ok": False, "error": str(exc)}), 500
    else:
        allowed = {
            "enabled", "bets_enabled", "slots_enabled", "slot_bankroll", "slot_bankroll_max", "slot_hourly_refill",
            "slot_min_bet", "slot_max_bet", "slot_cooldown_seconds",
            "coinflip_enabled", "polls_enabled", "quiz_enabled", "race_enabled", "target_enabled", "secret_enabled", "survival_enabled", "steal_enabled", "vault_enabled", "jackpot_enabled",
        }
        values = {key: data[key] for key in allowed if key in data}
        try:
            settings = update_settings(broadcaster_id, platform, values)
            games = ("slots","bets","coinflip","polls","quiz","race","target","secret","survival","steal","vault","jackpot")
            if "enabled" in values:
                for game_name in games:
                    set_minigame_commands_enabled(broadcaster_id, game_name, bool(settings["enabled"]) and bool(settings.get(f"{game_name}_enabled", True)))
            for game_name in games:
                field = f"{game_name}_enabled"
                if field in values:
                    set_minigame_commands_enabled(broadcaster_id, game_name, bool(settings["enabled"]) and bool(settings.get(field, True)))
        except (TypeError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except Exception as exc:
            print(f"[MINIGAMES] save erro: {exc}", flush=True)
            return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, "platform": platform, "settings": settings, "command_status": get_minigame_command_status(broadcaster_id)})
