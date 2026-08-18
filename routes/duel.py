import random
from flask import Blueprint, jsonify, request
from core.database import get_conn
from core.services import ensure_player, get_channel

duel_bp = Blueprint("duel", __name__)

@duel_bp.post("/<int:broadcaster_id>")
def duel(broadcaster_id):
    data = request.get_json(silent=True) or {}
    attacker = str(data.get("attacker", "")).strip().lstrip("@")
    defender = str(data.get("defender", "")).strip().lstrip("@")
    if not attacker or not defender or attacker.lower() == defender.lower():
        return jsonify({"ok": False, "error": "jogadores inválidos"}), 400

    channel = get_channel(broadcaster_id)
    ensure_player(broadcaster_id, attacker)
    ensure_player(broadcaster_id, defender)

    winner = random.choice([attacker, defender])
    loser = defender if winner == attacker else attacker
    win = channel["duel_win_points"]
    loss = channel["duel_loss_points"]

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('''UPDATE players
                           SET points=points+%s, duels=duels+1, streak=streak+1,
                               updated_at=NOW()
                           WHERE broadcaster_user_id=%s AND username=%s''',
                        (win, broadcaster_id, winner))
            cur.execute('''UPDATE players
                           SET points=GREATEST(0, points-%s), duels=duels+1,
                               streak=0, updated_at=NOW()
                           WHERE broadcaster_user_id=%s AND username=%s''',
                        (loss, broadcaster_id, loser))
            cur.execute('''INSERT INTO duel_events
                           (broadcaster_user_id, attacker, defender, winner,
                            winner_points_delta, loser_points_delta)
                           VALUES (%s,%s,%s,%s,%s,%s)''',
                        (broadcaster_id, attacker, defender, winner, win, -loss))
        conn.commit()
    finally:
        conn.close()

    return jsonify({"ok": True, "attacker": attacker, "defender": defender,
                    "winner": winner, "loser": loser,
                    "winner_points": win, "loser_points": loss,
                    "currency": channel["currency_name"],
                    "emoji": channel["currency_emoji"], "v_d": False})
