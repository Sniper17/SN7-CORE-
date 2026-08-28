import os
import psycopg
try:
    from psycopg_pool import ConnectionPool
except ImportError:
    ConnectionPool = None
from threading import Lock

_db_initialized = False
_db_init_lock = Lock()
_point_rewards_ready = False

DEFAULT_POINTS_RESPONSE = "$(user), você tem $(points) $(currency).$(emoji_text)$(rank_text)"

SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
    id BIGSERIAL PRIMARY KEY,
    broadcaster_user_id BIGINT UNIQUE NOT NULL,
    sn7_profile_id BIGINT,
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
    platform TEXT NOT NULL DEFAULT 'kick',
    kick_user_id BIGINT,
    username TEXT NOT NULL,
    points BIGINT NOT NULL DEFAULT 0,
    streak INTEGER NOT NULL DEFAULT 0,
    duels INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (broadcaster_user_id, platform, username)
);

ALTER TABLE players ADD COLUMN IF NOT EXISTS platform TEXT NOT NULL DEFAULT 'kick';
ALTER TABLE players DROP CONSTRAINT IF EXISTS players_broadcaster_user_id_username_key;
CREATE UNIQUE INDEX IF NOT EXISTS uq_players_channel_platform_username
ON players (broadcaster_user_id, platform, username);

CREATE INDEX IF NOT EXISTS idx_players_channel_points
ON players (broadcaster_user_id, platform, points DESC);

ALTER TABLE pending_bets ADD COLUMN IF NOT EXISTS platform TEXT NOT NULL DEFAULT 'kick';
ALTER TABLE duel_events ADD COLUMN IF NOT EXISTS platform TEXT NOT NULL DEFAULT 'kick';

CREATE INDEX IF NOT EXISTS idx_players_channel_kick_user
ON players (broadcaster_user_id, kick_user_id)
WHERE kick_user_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS store_items (
    id BIGSERIAL PRIMARY KEY,
    broadcaster_user_id BIGINT NOT NULL,
    item_type TEXT NOT NULL DEFAULT 'reward',
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    image_url TEXT NOT NULL DEFAULT '',
    audio_url TEXT NOT NULL DEFAULT '',
    price BIGINT NOT NULL CHECK (price > 0),
    stock BIGINT,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (item_type IN ('reward','audio'))
);
CREATE INDEX IF NOT EXISTS idx_store_items_channel_active
ON store_items(broadcaster_user_id, active, id DESC);

CREATE TABLE IF NOT EXISTS store_redemptions (
    id BIGSERIAL PRIMARY KEY,
    broadcaster_user_id BIGINT NOT NULL,
    item_id BIGINT NOT NULL REFERENCES store_items(id) ON DELETE CASCADE,
    viewer_kick_user_id BIGINT NOT NULL,
    viewer_username TEXT NOT NULL DEFAULT '',
    price BIGINT NOT NULL CHECK (price > 0),
    status TEXT NOT NULL DEFAULT 'queued',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    fulfilled_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_store_redemptions_channel
ON store_redemptions(broadcaster_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_store_redemptions_viewer
ON store_redemptions(viewer_kick_user_id, broadcaster_user_id, created_at DESC);

ALTER TABLE store_redemptions ADD COLUMN IF NOT EXISTS platform TEXT NOT NULL DEFAULT 'kick';
ALTER TABLE store_redemptions ADD COLUMN IF NOT EXISTS viewer_external_id TEXT NOT NULL DEFAULT '';
ALTER TABLE store_redemptions ALTER COLUMN viewer_kick_user_id DROP NOT NULL;
CREATE INDEX IF NOT EXISTS idx_store_redemptions_platform_viewer
ON store_redemptions(broadcaster_user_id, platform, viewer_external_id, created_at DESC);

CREATE TABLE IF NOT EXISTS store_audio_queue (
    id BIGSERIAL PRIMARY KEY,
    broadcaster_user_id BIGINT NOT NULL,
    redemption_id BIGINT NOT NULL REFERENCES store_redemptions(id) ON DELETE CASCADE,
    item_id BIGINT NOT NULL REFERENCES store_items(id) ON DELETE CASCADE,
    viewer_kick_user_id BIGINT NOT NULL,
    viewer_username TEXT NOT NULL DEFAULT '',
    audio_url TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_store_audio_queue_channel
ON store_audio_queue(broadcaster_user_id, status, id);

ALTER TABLE store_audio_queue ADD COLUMN IF NOT EXISTS platform TEXT NOT NULL DEFAULT 'kick';
ALTER TABLE store_audio_queue ADD COLUMN IF NOT EXISTS viewer_external_id TEXT NOT NULL DEFAULT '';
ALTER TABLE store_audio_queue ALTER COLUMN viewer_kick_user_id DROP NOT NULL;
CREATE INDEX IF NOT EXISTS idx_store_audio_queue_platform
ON store_audio_queue(broadcaster_user_id, platform, status, id);

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
    platform TEXT NOT NULL DEFAULT 'kick',
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
    platform TEXT NOT NULL DEFAULT 'kick',
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
    bot_active BOOLEAN NOT NULL DEFAULT FALSE,
    access_token TEXT,
    refresh_token TEXT,
    expires_at BIGINT NOT NULL DEFAULT 0,
    scope TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS chat_connections (
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
    bot_active BOOLEAN NOT NULL DEFAULT FALSE,
    cursor TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (broadcaster_user_id, provider)
);
CREATE INDEX IF NOT EXISTS idx_chat_connections_provider
    ON chat_connections(provider, bot_active);

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
    position_ms BIGINT NOT NULL DEFAULT 0,
    duration_ms BIGINT NOT NULL DEFAULT 0,
    seek_position_ms BIGINT NOT NULL DEFAULT 0,
    seek_revision BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE music_player_state ADD COLUMN IF NOT EXISTS position_ms BIGINT NOT NULL DEFAULT 0;
ALTER TABLE music_player_state ADD COLUMN IF NOT EXISTS duration_ms BIGINT NOT NULL DEFAULT 0;
ALTER TABLE music_player_state ADD COLUMN IF NOT EXISTS seek_position_ms BIGINT NOT NULL DEFAULT 0;
ALTER TABLE music_player_state ADD COLUMN IF NOT EXISTS seek_revision BIGINT NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS music_play_history (
    id BIGSERIAL PRIMARY KEY,
    broadcaster_user_id BIGINT NOT NULL,
    queue_id BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_music_play_history_channel
ON music_play_history(broadcaster_user_id,id DESC);

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

CREATE TABLE IF NOT EXISTS minigame_settings (
    broadcaster_user_id BIGINT NOT NULL,
    platform TEXT NOT NULL DEFAULT 'kick',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    bets_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    slots_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    slot_bankroll BIGINT NOT NULL DEFAULT 10000,
    slot_bankroll_max BIGINT NOT NULL DEFAULT 50000,
    slot_hourly_refill BIGINT NOT NULL DEFAULT 1000,
    slot_min_bet BIGINT NOT NULL DEFAULT 10,
    coinflip_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    polls_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    quiz_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    race_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    target_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    secret_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    survival_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    survival_duration_seconds INTEGER NOT NULL DEFAULT 90,
    survival_prize BIGINT NOT NULL DEFAULT 50,
    steal_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    vault_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    jackpot_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    slot_max_bet BIGINT NOT NULL DEFAULT 1000,
    slot_cooldown_seconds INTEGER NOT NULL DEFAULT 5,
    last_slot_refill_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (broadcaster_user_id, platform)
);
ALTER TABLE minigame_settings ADD COLUMN IF NOT EXISTS bets_enabled BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE minigame_settings ADD COLUMN IF NOT EXISTS slots_enabled BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE minigame_settings ADD COLUMN IF NOT EXISTS coinflip_enabled BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE minigame_settings ADD COLUMN IF NOT EXISTS polls_enabled BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE minigame_settings ADD COLUMN IF NOT EXISTS quiz_enabled BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE minigame_settings ADD COLUMN IF NOT EXISTS race_enabled BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE minigame_settings ADD COLUMN IF NOT EXISTS target_enabled BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE minigame_settings ADD COLUMN IF NOT EXISTS secret_enabled BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE minigame_settings ADD COLUMN IF NOT EXISTS survival_enabled BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE minigame_settings ADD COLUMN IF NOT EXISTS survival_duration_seconds INTEGER NOT NULL DEFAULT 90;
ALTER TABLE minigame_settings ADD COLUMN IF NOT EXISTS survival_prize BIGINT NOT NULL DEFAULT 50;
ALTER TABLE minigame_settings ADD COLUMN IF NOT EXISTS steal_enabled BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE minigame_settings ADD COLUMN IF NOT EXISTS vault_enabled BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE minigame_settings ADD COLUMN IF NOT EXISTS jackpot_enabled BOOLEAN NOT NULL DEFAULT TRUE;
CREATE INDEX IF NOT EXISTS idx_minigame_settings_channel
    ON minigame_settings(broadcaster_user_id, platform);

CREATE TABLE IF NOT EXISTS minigame_runtime (
    broadcaster_user_id BIGINT NOT NULL,
    platform TEXT NOT NULL,
    game TEXT NOT NULL,
    state JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (broadcaster_user_id, platform, game)
);


CREATE TABLE IF NOT EXISTS overlay_configs (
    broadcaster_user_id BIGINT PRIMARY KEY,
    config JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS overlay_events (
    id BIGSERIAL PRIMARY KEY,
    broadcaster_user_id BIGINT NOT NULL,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_overlay_events_channel_id
    ON overlay_events(broadcaster_user_id, id);

-- Safe migrations for databases created before Music OAuth/public controls.
ALTER TABLE music_settings
    ADD COLUMN IF NOT EXISTS public_commands BOOLEAN NOT NULL DEFAULT FALSE;
"""

def get_conn():
    """Abre uma conexão PostgreSQL por operação.

    O DATABASE_URL do SN7 aponta para o pooler do Neon. Um segundo pool local
    dentro do processo criava uma fila artificial: os workers de YouTube e
    automações podiam ocupar todas as conexões locais e os requests HTTP
    falhavam com "couldn't get a connection after ... sec".
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL não configurado.")
    return psycopg.connect(
        url,
        connect_timeout=int(os.environ.get("SN7_DB_CONNECT_TIMEOUT", "10")),
        application_name="SN7-Core",
    )


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
                cur.execute("ALTER TABLE kick_connections ADD COLUMN IF NOT EXISTS bot_active BOOLEAN NOT NULL DEFAULT FALSE")
                cur.execute("ALTER TABLE kick_connections ADD COLUMN IF NOT EXISTS sn7_profile_id BIGINT")
                cur.execute("UPDATE kick_connections SET sn7_profile_id=broadcaster_user_id WHERE sn7_profile_id IS NULL")
                cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_kick_connections_sn7_profile_id ON kick_connections(sn7_profile_id)")
                cur.execute("ALTER TABLE chat_connections ADD COLUMN IF NOT EXISTS bot_active BOOLEAN NOT NULL DEFAULT FALSE")
                cur.execute("ALTER TABLE chat_connections ADD COLUMN IF NOT EXISTS cursor TEXT NOT NULL DEFAULT ''")
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

def migrate_channel_id(old_id, new_id):
    """Move os dados de um canal temporário para o ID definitivo da Kick."""
    old_id = int(old_id)
    new_id = int(new_id)
    if old_id == new_id:
        return
    tables = [
        "channels", "players", "custom_commands", "duel_events", "pending_bets",
        "kick_connections", "chat_connections", "command_configs", "command_aliases",
        "obs_connections", "music_settings", "music_queue", "music_player_state",
        "music_connections", "point_rewards", "minigame_settings",
    ]
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # Se o ID definitivo já possui dados, não misturamos contas
            # silenciosamente. O chamador pode manter a conta antiga intacta.
            for table in tables:
                cur.execute(
                    f"SELECT 1 FROM {table} WHERE broadcaster_user_id=%s LIMIT 1",
                    (new_id,),
                )
                if cur.fetchone():
                    raise RuntimeError(
                        "O canal da Kick já possui dados no SN7 Core; "
                        "não foi feita a migração automática."
                    )
            for table in tables:
                cur.execute(
                    f"UPDATE {table} SET broadcaster_user_id=%s WHERE broadcaster_user_id=%s",
                    (new_id, old_id),
                )
        conn.commit()
    finally:
        conn.close()

# SN7_POINTS_REWARDS_V1
POINTS_REWARD_SCHEMA = """
CREATE TABLE IF NOT EXISTS point_rewards (
    broadcaster_user_id BIGINT PRIMARY KEY,
    watch_points INTEGER NOT NULL DEFAULT 1,
    watch_interval_minutes INTEGER NOT NULL DEFAULT 10,
    sub_bonus INTEGER NOT NULL DEFAULT 500,
    kicks_bonus_per_kick INTEGER NOT NULL DEFAULT 1,
    bits_bonus_per_bit INTEGER NOT NULL DEFAULT 1,
    superchat_bonus_per_unit INTEGER NOT NULL DEFAULT 1,
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
            cur.execute("""
                ALTER TABLE point_rewards
                ADD COLUMN IF NOT EXISTS bits_bonus_per_bit INTEGER NOT NULL DEFAULT 1
            """)
            cur.execute("""
                ALTER TABLE point_rewards
                ADD COLUMN IF NOT EXISTS superchat_bonus_per_unit INTEGER NOT NULL DEFAULT 1
            """)
        if own_connection:
            conn.commit()
    finally:
        if own_connection:
            conn.close()
