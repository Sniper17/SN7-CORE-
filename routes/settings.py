from flask import Blueprint, jsonify, request
from core.services import get_channel, ensure_channel, get_point_rewards, update_point_rewards
from core.database import get_conn
from core.command_system import update_command

settings_bp = Blueprint("settings", __name__)

DEFAULT_SETTINGS = {
    "broadcaster_user_id": 1,
    "username": "",
    "currency_name": "Points",
    "currency_command": "!points",
    "currency_emoji": "",
    "points_response": "$(user), você tem $(points) $(currency).$(emoji_text)$(rank_text)",
    "rank_title": "Ranking",
    "rank_limit": 5,
    "duel_win_points": 10,
    "duel_loss_points": 3,
    "watch_points": 1,
    "watch_interval_minutes": 10,
    "sub_bonus": 500,
    "kicks_bonus_per_kick": 1,
}

@settings_bp.get("/<int:broadcaster_id>")
def get_settings(broadcaster_id):
    try:
        settings = get_channel(broadcaster_id)
        settings["point_rewards"] = get_point_rewards(broadcaster_id)
        return jsonify({"ok": True, "settings": settings, "demo": False})
    except RuntimeError as exc:
        if "DATABASE_URL" in str(exc):
            demo = dict(DEFAULT_SETTINGS)
            demo["broadcaster_user_id"] = broadcaster_id
            demo["point_rewards"] = {"watch_points": 1, "watch_interval_minutes": 10, "sub_bonus": 500, "kicks_bonus_per_kick": 1}
            return jsonify({"ok": True, "settings": demo, "demo": True})
        raise

@settings_bp.put("/<int:broadcaster_id>")
def update_settings(broadcaster_id):
    data = request.get_json(silent=True) or {}
    allowed = {
        "currency_name", "currency_command", "currency_emoji", "points_response",
        "rank_title", "rank_limit", "duel_win_points", "duel_loss_points",
        "watch_points", "watch_interval_minutes", "sub_bonus", "kicks_bonus_per_kick"
    }
    values = {k: data[k] for k in allowed if k in data}

    if not values:
        return jsonify({"ok": False, "error": "nenhuma alteração"}), 400

    if "currency_command" in values:
        values["currency_command"] = str(values["currency_command"]).strip().lower()
        if not values["currency_command"].startswith("!"):
            return jsonify({"ok": False, "error": "O comando deve começar com !"}), 400

    if "points_response" in values:
        values["points_response"] = str(values["points_response"])
        if not values["points_response"].strip():
            return jsonify({"ok": False, "error": "A mensagem do saldo não pode ficar vazia."}), 400
        if len(values["points_response"]) > 500:
            return jsonify({"ok": False, "error": "A mensagem do saldo pode ter no máximo 500 caracteres."}), 400

    try:
        ensure_channel(broadcaster_id)

        reward_values = {k: values.pop(k) for k in list(values) if k in {"watch_points", "watch_interval_minutes", "sub_bonus", "kicks_bonus_per_kick"}}
        if reward_values:
            update_point_rewards(broadcaster_id, reward_values)

        if "currency_command" in values or "points_response" in values:
            update_command(
                broadcaster_id,
                "points",
                command=values.get("currency_command"),
                response=values.get("points_response"),
            )

        channel_values = {
            k: v for k, v in values.items()
            if k not in {"currency_command", "points_response"}
        }

        if "points_response" in values:
            channel_values["points_response"] = values["points_response"]

        if channel_values:
            sets = ", ".join(f"{k}=%s" for k in channel_values)
            params = list(channel_values.values()) + [broadcaster_id]
            conn = get_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        f"UPDATE channels SET {sets}, updated_at=NOW() "
                        "WHERE broadcaster_user_id=%s",
                        params
                    )
                conn.commit()
            finally:
                conn.close()

        return jsonify({
            "ok": True,
            "settings": {**get_channel(broadcaster_id), "point_rewards": get_point_rewards(broadcaster_id)},
            "demo": False
        })

    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except RuntimeError as exc:
        if "DATABASE_URL" in str(exc):
            return jsonify({
                "ok": False,
                "demo": True,
                "error": "Modo demonstração: conecte um banco PostgreSQL no Render para salvar as alterações."
            }), 503
        raise
