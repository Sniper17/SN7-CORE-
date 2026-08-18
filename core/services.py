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
