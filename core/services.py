from core.database import get_conn
from core.command_system import ensure_command_defaults


def ensure_channel(broadcaster_id, username=""):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO channels (broadcaster_user_id, username)
                VALUES (%s, %s)
                ON CONFLICT (broadcaster_user_id)
                DO UPDATE SET username=CASE
                    WHEN EXCLUDED.username <> '' THEN EXCLUDED.username
                    ELSE channels.username END,
                    updated_at=NOW()
                """,
                (int(broadcaster_id), username or "")
            )
        conn.commit()
    finally:
        conn.close()
    ensure_command_defaults(broadcaster_id)


def get_channel(broadcaster_id):
    ensure_channel(broadcaster_id)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT broadcaster_user_id, username, currency_name,
                       currency_command, currency_emoji, points_response,
                       rank_title, rank_limit, duel_win_points, duel_loss_points
                  FROM channels
                 WHERE broadcaster_user_id=%s
                """,
                (int(broadcaster_id),)
            )
            row = cur.fetchone()
            if not row:
                return None
            keys = [
                "broadcaster_user_id", "username", "currency_name",
                "currency_command", "currency_emoji", "points_response",
                "rank_title", "rank_limit", "duel_win_points", "duel_loss_points"
            ]
            return dict(zip(keys, row))
    finally:
        conn.close()


def ensure_player(broadcaster_id, username, kick_user_id=None):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO players
                    (broadcaster_user_id, kick_user_id, username)
                VALUES (%s,%s,%s)
                ON CONFLICT (broadcaster_user_id, username)
                DO UPDATE SET kick_user_id=COALESCE(
                    EXCLUDED.kick_user_id, players.kick_user_id),
                    updated_at=NOW()
                """,
                (int(broadcaster_id), kick_user_id, username)
            )
        conn.commit()
    finally:
        conn.close()


def get_player(broadcaster_id, username):
    ensure_player(broadcaster_id, username)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT username, points, streak, duels
                  FROM players
                 WHERE broadcaster_user_id=%s AND username=%s
                """,
                (int(broadcaster_id), username)
            )
            row = cur.fetchone()
            return (
                {
                    "username": row[0],
                    "points": row[1],
                    "streak": row[2],
                    "duels": row[3]
                }
                if row else None
            )
    finally:
        conn.close()


def get_rank(broadcaster_id, username):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT points
                  FROM players
                 WHERE broadcaster_user_id=%s AND username=%s
                """,
                (int(broadcaster_id), username)
            )
            row = cur.fetchone()
            if not row or int(row[0] or 0) <= 0:
                return None

            cur.execute(
                """
                SELECT COUNT(*) + 1
                  FROM players p
                 WHERE p.broadcaster_user_id=%s
                   AND p.points>0
                   AND p.points>
                       (SELECT points
                          FROM players
                         WHERE broadcaster_user_id=%s AND username=%s)
                """,
                (int(broadcaster_id), int(broadcaster_id), username)
            )
            return cur.fetchone()[0]
    finally:
        conn.close()

# SN7_POINTS_REWARDS_V1
def get_point_rewards(broadcaster_id):
    ensure_point_rewards_table()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO point_rewards (broadcaster_user_id)
                VALUES (%s) ON CONFLICT (broadcaster_user_id) DO NOTHING
            """, (int(broadcaster_id),))
            cur.execute("""
                SELECT watch_points, watch_interval_minutes, sub_bonus, kicks_bonus_per_kick
                FROM point_rewards WHERE broadcaster_user_id=%s
            """, (int(broadcaster_id),))
            row = cur.fetchone()
            conn.commit()
            return {"watch_points": int(row[0]), "watch_interval_minutes": int(row[1]), "sub_bonus": int(row[2]), "kicks_bonus_per_kick": int(row[3])}
    finally:
        conn.close()

def update_point_rewards(broadcaster_id, values):
    ensure_point_rewards_table()
    clean = {}
    for key in ("watch_points", "watch_interval_minutes", "sub_bonus", "kicks_bonus_per_kick"):
        if key in values:
            try: value = int(values[key])
            except (TypeError, ValueError): raise ValueError(f"{key} precisa ser um número.")
            if value < 0: raise ValueError(f"{key} não pode ser negativo.")
            if key == "watch_interval_minutes" and not 1 <= value <= 240: raise ValueError("O intervalo de presença deve ficar entre 1 e 240 minutos.")
            clean[key] = value
    if not clean: return get_point_rewards(broadcaster_id)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO point_rewards (broadcaster_user_id) VALUES (%s) ON CONFLICT (broadcaster_user_id) DO NOTHING", (int(broadcaster_id),))
            sets = ", ".join(f"{k}=%s" for k in clean)
            params = list(clean.values()) + [int(broadcaster_id)]
            cur.execute(f"UPDATE point_rewards SET {sets}, updated_at=NOW() WHERE broadcaster_user_id=%s", params)
        conn.commit()
    finally: conn.close()
    return get_point_rewards(broadcaster_id)

def add_points(broadcaster_id, username, amount, kick_user_id=None):
    amount = int(amount)
    if amount == 0: return
    ensure_player(broadcaster_id, username, kick_user_id)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE players SET points=GREATEST(0,points+%s), updated_at=NOW() WHERE broadcaster_user_id=%s AND username=%s", (amount,int(broadcaster_id),username))
        conn.commit()
    finally: conn.close()

def award_watch_presence(broadcaster_id, username, kick_user_id=None):
    rewards = get_point_rewards(broadcaster_id)
    if rewards["watch_points"] <= 0: return 0
    ensure_player(broadcaster_id, username, kick_user_id)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE players
                   SET points=points+%s, last_view_reward_at=NOW(), updated_at=NOW()
                 WHERE broadcaster_user_id=%s AND username=%s
                   AND (last_view_reward_at IS NULL OR last_view_reward_at <= NOW() - (%s * INTERVAL '1 minute'))
                RETURNING points
            """, (rewards["watch_points"], int(broadcaster_id), username, rewards["watch_interval_minutes"]))
            row = cur.fetchone()
        conn.commit()
        return rewards["watch_points"] if row else 0
    finally: conn.close()
