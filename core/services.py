from core.database import get_conn, ensure_point_rewards_table
from core.command_system import ensure_command_defaults
from core.cache import (
    get_player_identity,
    remember_player_identity,
    get_cached_rewards,
    set_cached_rewards,
    forget_rewards,
    get_cached_channel,
    set_cached_channel,
    forget_channel,
)


def ensure_channel(broadcaster_id, username=""):
    bid = int(broadcaster_id)
    cached = get_cached_channel(bid)
    if cached and (not username or cached.get("username") == str(username)):
        return
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
    # A configuração completa é carregada sob demanda; aqui só garantimos o canal.
    ensure_command_defaults(bid)
    forget_channel(bid)


def get_channel(broadcaster_id):
    bid = int(broadcaster_id)
    cached = get_cached_channel(bid)
    if cached is not None:
        return cached
    ensure_channel(bid)
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
            value = dict(zip(keys, row))
            set_cached_channel(bid, value)
            return value
    finally:
        conn.close()


def ensure_player(broadcaster_id, username, kick_user_id=None):
    """Garante o cadastro sem usar o nick como identidade.

    Depois do primeiro encontro, as mensagens normais não consultam o banco:
    o cache identifica o usuário pelo ID permanente da Kick. Se o nick mudar,
    somente nesse momento fazemos uma atualização no PostgreSQL.
    """
    bid = int(broadcaster_id)
    name = str(username or "").strip()
    uid = int(kick_user_id) if kick_user_id is not None else None
    cached = get_player_identity(bid, uid, name)

    if cached:
        if cached.get("username") == name:
            return
        # Mesmo usuário, novo nick: atualiza apenas a identidade persistente.
        if uid is not None:
            conn = get_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE players
                           SET username=%s, updated_at=NOW()
                         WHERE broadcaster_user_id=%s AND kick_user_id=%s
                        """,
                        (name, bid, uid),
                    )
                conn.commit()
            finally:
                conn.close()
        remember_player_identity(bid, uid, name)
        return

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            row = None
            if uid is not None:
                cur.execute(
                    """
                    SELECT id, username
                      FROM players
                     WHERE broadcaster_user_id=%s AND kick_user_id=%s
                     LIMIT 1
                    FOR UPDATE
                    """,
                    (bid, uid),
                )
                row = cur.fetchone()

            if row:
                if str(row[1]) != name:
                    cur.execute(
                        """
                        UPDATE players
                           SET username=%s, updated_at=NOW()
                         WHERE id=%s
                        """,
                        (name, row[0]),
                    )
            else:
                cur.execute(
                    """
                    SELECT id, kick_user_id
                      FROM players
                     WHERE broadcaster_user_id=%s AND username=%s
                     LIMIT 1
                    FOR UPDATE
                    """,
                    (bid, name),
                )
                row = cur.fetchone()

                if row:
                    if uid is not None and row[1] != uid:
                        cur.execute(
                            """
                            UPDATE players
                               SET kick_user_id=%s, updated_at=NOW()
                             WHERE id=%s
                            """,
                            (uid, row[0]),
                        )
                else:
                    cur.execute(
                        """
                        INSERT INTO players
                            (broadcaster_user_id, kick_user_id, username)
                        VALUES (%s,%s,%s)
                        """,
                        (bid, uid, name),
                    )
        conn.commit()
    finally:
        conn.close()

    remember_player_identity(bid, uid, name)


def get_player(broadcaster_id, username):
    # ensure_player só consulta o banco na primeira vez ou quando o nick muda.
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


def get_point_rewards(broadcaster_id):
    cached = get_cached_rewards(broadcaster_id)
    if cached is not None:
        return cached

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
            value = {
                "watch_points": int(row[0]),
                "watch_interval_minutes": int(row[1]),
                "sub_bonus": int(row[2]),
                "kicks_bonus_per_kick": int(row[3]),
            }
            set_cached_rewards(broadcaster_id, value)
            return value
    finally:
        conn.close()


def update_point_rewards(broadcaster_id, values):
    ensure_point_rewards_table()
    clean = {}
    for key in ("watch_points", "watch_interval_minutes", "sub_bonus", "kicks_bonus_per_kick"):
        if key in values:
            try:
                value = int(values[key])
            except (TypeError, ValueError):
                raise ValueError(f"{key} precisa ser um número.")
            if value < 0:
                raise ValueError(f"{key} não pode ser negativo.")
            if key == "watch_interval_minutes" and not 1 <= value <= 240:
                raise ValueError("O intervalo de presença deve ficar entre 1 e 240 minutos.")
            clean[key] = value
    if not clean:
        return get_point_rewards(broadcaster_id)

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO point_rewards (broadcaster_user_id) VALUES (%s) "
                "ON CONFLICT (broadcaster_user_id) DO NOTHING",
                (int(broadcaster_id),),
            )
            sets = ", ".join(f"{k}=%s" for k in clean)
            params = list(clean.values()) + [int(broadcaster_id)]
            cur.execute(
                f"UPDATE point_rewards SET {sets}, updated_at=NOW() "
                "WHERE broadcaster_user_id=%s",
                params,
            )
        conn.commit()
    finally:
        conn.close()

    forget_rewards(broadcaster_id)
    return get_point_rewards(broadcaster_id)


def add_points(broadcaster_id, username, amount, kick_user_id=None):
    amount = int(amount)
    if amount == 0:
        return
    ensure_player(broadcaster_id, username, kick_user_id)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE players
                   SET points=GREATEST(0,points+%s), updated_at=NOW()
                 WHERE broadcaster_user_id=%s AND username=%s
                """,
                (amount, int(broadcaster_id), username),
            )
        conn.commit()
    finally:
        conn.close()


def award_watch_presence(broadcaster_id, username, kick_user_id=None):
    rewards = get_point_rewards(broadcaster_id)
    if rewards["watch_points"] <= 0:
        return 0

    # Nenhuma leitura de cadastro por mensagem; o cache resolve usuários já vistos.
    ensure_player(broadcaster_id, username, kick_user_id)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE players
                   SET points=points+%s, last_view_reward_at=NOW(), updated_at=NOW()
                 WHERE broadcaster_user_id=%s AND username=%s
                   AND (
                       last_view_reward_at IS NULL
                       OR last_view_reward_at <= NOW() - (%s * INTERVAL '1 minute')
                   )
                RETURNING points
                """,
                (
                    rewards["watch_points"],
                    int(broadcaster_id),
                    username,
                    rewards["watch_interval_minutes"],
                ),
            )
            row = cur.fetchone()
        conn.commit()
        return rewards["watch_points"] if row else 0
    finally:
        conn.close()
