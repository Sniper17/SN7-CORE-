import os
import psycopg
from threading import Lock

_db_initialized = False
_db_init_lock = Lock()
_point_rewards_ready = False

DEFAULT_POINTS_RESPONSE = "$(user), você tem $(points) $(currency).$(emoji_text)$(rank_text)"

SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
    id BIGSERIAL PRIMARY KEY,
    broadcaster_user_id BIGINT UNIQUE NOT NULL,
    username TEXT NOT NULL DEFAULT '',
    currency_name TEXT NOT NULL DEFAULT 'Pontos',
    currency_command TEXT NOT NULL DEFAULT '!pontos',
    currency_emoji TEXT NOT NULL DEFAULT '',
    points_response TEXT NOT NULL DEFAULT '$(user), você tem $(points) $(currency).$(emoji_text)$(rank_text)',
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

CREATE INDEX IF NOT EXISTS idx_players_channel_kick_user
ON players (broadcaster_user_id, kick_user_id)
WHERE kick_user_id IS NOT NULL;

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

CREATE TABLE IF NOT EXISTS pending_bets (
    id BIGSERIAL PRIMARY KEY,
    broadcaster_user_id BIGINT NOT NULL,
    challenger TEXT NOT NULL,
    defender TEXT NOT NULL,
    amount BIGINT NOT NULL CHECK (amount > 0),
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '90 seconds')
);

CREATE INDEX IF NOT EXISTS idx_pending_bets_lookup
ON pending_bets (broadcaster_user_id, defender, status, created_at DESC);

CREATE TABLE IF NOT EXISTS kick_connections (
    id BIGSERIAL PRIMARY KEY,
    broadcaster_user_id BIGINT UNIQUE NOT NULL,
    username TEXT NOT NULL DEFAULT '',
    profile_picture_url TEXT NOT NULL DEFAULT '',
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


CREATE TABLE IF NOT EXISTS obs_connections (
    id BIGSERIAL PRIMARY KEY,
    broadcaster_user_id BIGINT UNIQUE NOT NULL,
    access_token TEXT UNIQUE NOT NULL,
    label TEXT NOT NULL DEFAULT 'SN7 Core',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_obs_connections_channel
ON obs_connections(broadcaster_user_id);

CREATE TABLE IF NOT EXISTS music_settings (
    broadcaster_user_id BIGINT PRIMARY KEY,
    allow_youtube BOOLEAN NOT NULL DEFAULT TRUE,
    allow_spotify BOOLEAN NOT NULL DEFAULT TRUE,
    allow_soundcloud BOOLEAN NOT NULL DEFAULT FALSE,
    allow_links BOOLEAN NOT NULL DEFAULT TRUE,
    public_commands BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS music_queue (
    id BIGSERIAL PRIMARY KEY,
    broadcaster_user_id BIGINT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'unknown',
    title TEXT NOT NULL,
    artist TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    added_by TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'queued',
    position INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_music_queue_channel ON music_queue(broadcaster_user_id,status,position,id);

CREATE TABLE IF NOT EXISTS music_player_state (
    broadcaster_user_id BIGINT PRIMARY KEY,
    current_queue_id BIGINT,
    is_playing BOOLEAN NOT NULL DEFAULT FALSE,
    volume INTEGER NOT NULL DEFAULT 80 CHECK (volume BETWEEN 0 AND 100),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS music_connections (
    id BIGSERIAL PRIMARY KEY,
    broadcaster_user_id BIGINT NOT NULL,
    provider TEXT NOT NULL,
    external_user_id TEXT NOT NULL DEFAULT '',
    username TEXT NOT NULL DEFAULT '',
    display_name TEXT NOT NULL DEFAULT '',
    profile_url TEXT NOT NULL DEFAULT '',
    avatar_url TEXT NOT NULL DEFAULT '',
    access_token TEXT NOT NULL,
    refresh_token TEXT,
    expires_at BIGINT NOT NULL DEFAULT 0,
    scope TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (broadcaster_user_id, provider)
);
CREATE INDEX IF NOT EXISTS idx_music_connections_channel
    ON music_connections(broadcaster_user_id, provider);

-- Safe migrations for databases created before Music OAuth/public controls.
ALTER TABLE music_settings
    ADD COLUMN IF NOT EXISTS public_commands BOOLEAN NOT NULL DEFAULT FALSE;
"""

def get_conn():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL não configurado.")
    return psycopg.connect(url)

def init_db():
    global _db_initialized, _point_rewards_ready
    if _db_initialized:
        return
    with _db_init_lock:
        if _db_initialized:
            return
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(SCHEMA)
                cur.execute("ALTER TABLE music_connections ADD COLUMN IF NOT EXISTS avatar_url TEXT NOT NULL DEFAULT ''")
                cur.execute("ALTER TABLE kick_connections ADD COLUMN IF NOT EXISTS profile_picture_url TEXT NOT NULL DEFAULT ''")
                ensure_point_rewards_table(conn)
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
                    UPDATE channels SET currency_emoji = '' WHERE currency_emoji = '🪙'
                """)
    
                default_sql = DEFAULT_POINTS_RESPONSE.replace("'", "''")
                cur.execute(f"""
                    ALTER TABLE channels
                    ALTER COLUMN points_response SET DEFAULT '{default_sql}'
                """)
    
    
    
                # SN7_POINTS_DEFAULT_MIGRATION_V3
                # Corrige registros antigos sem sobrescrever uma personalização válida.
                cur.execute("""
                    UPDATE channels
                       SET currency_name = 'Pontos',
                           updated_at = NOW()
                     WHERE currency_name IS NULL
                        OR BTRIM(currency_name) = ''
                        OR currency_name = 'Points'
                """)
    
                # Nunca sobrescreva um comando personalizado salvo pelo streamer.
                # Apenas valores realmente vazios recebem o padrão.
                cur.execute("""
                    UPDATE channels
                       SET currency_command = '!pontos',
                           updated_at = NOW()
                     WHERE currency_command IS NULL OR BTRIM(currency_command) = ''
                """)
    
                cur.execute("""
                    UPDATE command_configs
                       SET command = '!pontos',
                           response = %s,
                           updated_at = NOW()
                     WHERE command_key = 'points'
                       AND (command IS NULL OR BTRIM(command) = '')
                """, (DEFAULT_POINTS_RESPONSE,))
    
                cur.execute("""
                    ALTER TABLE channels
                    ALTER COLUMN points_response SET NOT NULL
                """)
    
                # Apostas pendentes expiram em 90 segundos. Atualiza também registros
                # antigos ainda pendentes para respeitar a nova regra sem mexer em pontos.
                cur.execute("""
                    ALTER TABLE pending_bets
                    ALTER COLUMN expires_at SET DEFAULT (NOW() + INTERVAL '90 seconds')
                """)
                cur.execute("""
                    UPDATE pending_bets
                       SET expires_at = created_at + INTERVAL '90 seconds'
                     WHERE status='pending'
                       AND expires_at > created_at + INTERVAL '90 seconds'
                """)
            conn.commit()
        finally:
            conn.close()
    
        _db_initialized = True
        _point_rewards_ready = True
# SN7_POINTS_REWARDS_V1
POINTS_REWARD_SCHEMA = """
CREATE TABLE IF NOT EXISTS point_rewards (
    broadcaster_user_id BIGINT PRIMARY KEY,
    watch_points INTEGER NOT NULL DEFAULT 1,
    watch_interval_minutes INTEGER NOT NULL DEFAULT 10,
    sub_bonus INTEGER NOT NULL DEFAULT 500,
    kicks_bonus_per_kick INTEGER NOT NULL DEFAULT 1,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

def ensure_point_rewards_table(conn=None):
    # init_db() já garante o schema no boot. Evita repetir CREATE/ALTER
    # em cada GET/PUT de configurações.
    global _point_rewards_ready
    if conn is None and _point_rewards_ready:
        return

    own_connection = conn is None
    if own_connection:
        conn = get_conn()

    try:
        with conn.cursor() as cur:
            cur.execute(POINTS_REWARD_SCHEMA)
            cur.execute("""
                ALTER TABLE players
                ADD COLUMN IF NOT EXISTS last_view_reward_at TIMESTAMPTZ
            """)
        if own_connection:
            conn.commit()
    finally:
        if own_connection:
            conn.close()
