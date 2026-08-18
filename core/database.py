import os
import psycopg

DEFAULT_POINTS_RESPONSE = "$(user), você tem $(points) $(currency). $(emoji) Sua posição no ranking é #$(rank)."

SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
    id BIGSERIAL PRIMARY KEY,
    broadcaster_user_id BIGINT UNIQUE NOT NULL,
    username TEXT NOT NULL DEFAULT '',
    currency_name TEXT NOT NULL DEFAULT 'Placos',
    currency_command TEXT NOT NULL DEFAULT '!placos',
    currency_emoji TEXT NOT NULL DEFAULT '🪙',
    points_response TEXT NOT NULL DEFAULT '$(user), você tem $(points) $(currency). $(emoji) Sua posição no ranking é #$(rank).',
    rank_title TEXT NOT NULL DEFAULT 'Ranking',
    rank_limit INTEGER NOT NULL DEFAULT 5,
    duel_win_points INTEGER NOT NULL DEFAULT 10,
    duel_loss_points INTEGER NOT NULL DEFAULT 3,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS players (
    id BIGSERIAL PRIMARY KEY,
    broadcaster_user_id BIGINT NOT NULL,
    kick_user_id BIGINT,
    username TEXT NOT NULL,
    points BIGINT NOT NULL DEFAULT 0,
    streak INTEGER NOT NULL DEFAULT 0,
    duels INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (broadcaster_user_id, username)
);

CREATE INDEX IF NOT EXISTS idx_players_channel_points
ON players (broadcaster_user_id, points DESC);

CREATE TABLE IF NOT EXISTS custom_commands (
    id BIGSERIAL PRIMARY KEY,
    broadcaster_user_id BIGINT NOT NULL,
    command TEXT NOT NULL,
    response TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (broadcaster_user_id, command)
);

CREATE TABLE IF NOT EXISTS duel_events (
    id BIGSERIAL PRIMARY KEY,
    broadcaster_user_id BIGINT NOT NULL,
    attacker TEXT NOT NULL,
    defender TEXT NOT NULL,
    winner TEXT NOT NULL,
    winner_points_delta INTEGER NOT NULL,
    loser_points_delta INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS kick_connections (
    id BIGSERIAL PRIMARY KEY,
    broadcaster_user_id BIGINT UNIQUE NOT NULL,
    username TEXT NOT NULL DEFAULT '',
    access_token TEXT,
    refresh_token TEXT,
    expires_at BIGINT NOT NULL DEFAULT 0,
    scope TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS kick_webhook_events (
    message_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL DEFAULT '',
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS command_configs (
 id BIGSERIAL PRIMARY KEY, broadcaster_user_id BIGINT NOT NULL, command_key TEXT NOT NULL,
 command TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', response TEXT NOT NULL DEFAULT '',
 enabled BOOLEAN NOT NULL DEFAULT TRUE, category TEXT NOT NULL DEFAULT 'public', is_system BOOLEAN NOT NULL DEFAULT FALSE,
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 UNIQUE(broadcaster_user_id,command_key), UNIQUE(broadcaster_user_id,command)
);

CREATE TABLE IF NOT EXISTS command_aliases (
 id BIGSERIAL PRIMARY KEY, broadcaster_user_id BIGINT NOT NULL,
 command_id BIGINT NOT NULL REFERENCES command_configs(id) ON DELETE CASCADE, alias TEXT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), UNIQUE(broadcaster_user_id,alias)
);

CREATE INDEX IF NOT EXISTS idx_command_configs_channel ON command_configs(broadcaster_user_id,category,enabled);
CREATE INDEX IF NOT EXISTS idx_command_aliases_channel ON command_aliases(broadcaster_user_id,alias);
"""

def get_conn():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL não configurado.")
    return psycopg.connect(url)

def init_db():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA)
            cur.execute("""
                ALTER TABLE channels
                ADD COLUMN IF NOT EXISTS points_response TEXT
            """)

            cur.execute("""
                UPDATE channels
                SET points_response = %s
                WHERE points_response IS NULL OR BTRIM(points_response) = ''
            """, (DEFAULT_POINTS_RESPONSE,))

            cur.execute("""
                ALTER TABLE channels
                ALTER COLUMN points_response SET DEFAULT %s
            """, (DEFAULT_POINTS_RESPONSE,))

            cur.execute("""
                ALTER TABLE channels
                ALTER COLUMN points_response SET NOT NULL
            """)
        conn.commit()
    finally:
        conn.close()
