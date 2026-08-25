import random
import time
from datetime import datetime, timezone
from threading import RLock

from core.cache import forget_rankings
from core.database import get_conn
from core.services import ensure_player

SUPPORTED_PLATFORMS = {"kick", "twitch", "youtube"}
DEFAULTS = {
    "enabled": True,
    "bets_enabled": True,
    "slots_enabled": True,
    "slot_bankroll": 10000,
    "slot_bankroll_max": 50000,
    "slot_hourly_refill": 1000,
    "slot_min_bet": 10,
    "slot_max_bet": 1000,
    "slot_cooldown_seconds": 5,
    "coinflip_enabled": True,
    "polls_enabled": True,
    "quiz_enabled": True,
    "race_enabled": True,
    "target_enabled": True,
    "secret_enabled": True,
    "survival_enabled": True,
    "steal_enabled": True,
    "vault_enabled": True,
    "jackpot_enabled": True,
}

# Configurações de minigames são estáveis durante a live. Evitamos consultar
# e migrar o schema do PostgreSQL a cada comando (era um dos maiores gargalos
# de !cara/!coroa e dos demais minigames).
_MINIGAME_SCHEMA_READY = False
_MINIGAME_RUNTIME_READY = False
_MINIGAME_SCHEMA_LOCK = RLock()
_SETTINGS_CACHE = {}
_SETTINGS_CACHE_TTL = 10.0

# Slots favor the house. We choose the outcome category first so matching
# symbols remain rare and the channel economy stays sustainable.
SLOT_FRUITS = (
    "🍒", "🍋", "🍊", "🍇", "🍉", "🍓", "🌶️"
)
SLOT_SPECIALS = ("🔔", "⭐")
SLOT_SYMBOLS = SLOT_FRUITS + SLOT_SPECIALS

# Weights are intentionally conservative: most rolls lose, pairs return only
# a small fraction of the stake, and the large wins are genuinely rare.
SLOT_OUTCOME_WEIGHTS = {
    "loss": 7800,
    "pair_fruit": 1700,
    "pair_special": 300,
    "triple_fruit": 150,
    "triple_diamond": 40,
    "triple_seven": 10,
}


def _make_slots_roll():
    outcome = random.choices(
        tuple(SLOT_OUTCOME_WEIGHTS),
        weights=tuple(SLOT_OUTCOME_WEIGHTS.values()),
        k=1,
    )[0]

    if outcome == "loss":
        symbols = random.sample(SLOT_SYMBOLS, 3)
        return symbols, 0.0, "loss"

    if outcome == "pair_fruit":
        pair = random.choice(SLOT_FRUITS)
        other = random.choice(tuple(symbol for symbol in SLOT_SYMBOLS if symbol != pair))
        symbols = [pair, pair, other]
        random.shuffle(symbols)
        return symbols, 0.25, "pair"

    if outcome == "pair_special":
        pair = random.choice(SLOT_SPECIALS)
        other = random.choice(SLOT_FRUITS)
        symbols = [pair, pair, other]
        random.shuffle(symbols)
        return symbols, 0.50, "pair_special"

    if outcome == "triple_fruit":
        fruit = random.choice(SLOT_FRUITS)
        return [fruit, fruit, fruit], 3.0, "triple"

    if outcome == "triple_diamond":
        return ["💎", "💎", "💎"], 2.0, "diamond"

    return ["7️⃣", "7️⃣", "7️⃣"], 5.0, "jackpot"


_COOLDOWN = {}


def _platform(platform):
    value = str(platform or "kick").strip().lower()
    return value if value in SUPPORTED_PLATFORMS else "kick"


def _normalize(values):
    result = dict(DEFAULTS)
    for key in DEFAULTS:
        if key in values:
            if key in {"enabled", "bets_enabled", "slots_enabled"}:
                result[key] = bool(values[key])
            else:
                result[key] = int(values[key])
    result["slot_bankroll"] = max(0, result["slot_bankroll"])
    result["slot_bankroll_max"] = max(result["slot_bankroll"], result["slot_bankroll_max"])
    result["slot_hourly_refill"] = max(0, result["slot_hourly_refill"])
    result["slot_min_bet"] = max(1, result["slot_min_bet"])
    result["slot_max_bet"] = max(result["slot_min_bet"], result["slot_max_bet"])
    result["slot_cooldown_seconds"] = max(1, min(60, result["slot_cooldown_seconds"]))
    return result


def ensure_minigame_table():
    """Garante o schema uma única vez por processo.

    A versão anterior executava CREATE/ALTER/INDEX em toda chamada de
    get_settings(), fazendo cada comando financeiro pagar o custo de uma
    migração de schema. O boot do SN7 já inicializa o banco; aqui mantemos uma
    proteção para bancos antigos, mas só executamos essa rotina uma vez.
    """
    global _MINIGAME_SCHEMA_READY
    if _MINIGAME_SCHEMA_READY:
        return
    with _MINIGAME_SCHEMA_LOCK:
        if _MINIGAME_SCHEMA_READY:
            return
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
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
                        steal_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                        vault_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                        jackpot_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                        slot_max_bet BIGINT NOT NULL DEFAULT 1000,
                        slot_cooldown_seconds INTEGER NOT NULL DEFAULT 5,
                        last_slot_refill_at TIMESTAMPTZ,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        PRIMARY KEY (broadcaster_user_id, platform)
                    )
                    """
                )
                for col, definition in (
                    ("bets_enabled", "BOOLEAN NOT NULL DEFAULT TRUE"),
                    ("slots_enabled", "BOOLEAN NOT NULL DEFAULT TRUE"),
                    ("coinflip_enabled", "BOOLEAN NOT NULL DEFAULT TRUE"),
                    ("polls_enabled", "BOOLEAN NOT NULL DEFAULT TRUE"),
                    ("quiz_enabled", "BOOLEAN NOT NULL DEFAULT TRUE"),
                    ("race_enabled", "BOOLEAN NOT NULL DEFAULT TRUE"),
                    ("target_enabled", "BOOLEAN NOT NULL DEFAULT TRUE"),
                    ("secret_enabled", "BOOLEAN NOT NULL DEFAULT TRUE"),
                    ("survival_enabled", "BOOLEAN NOT NULL DEFAULT TRUE"),
                    ("steal_enabled", "BOOLEAN NOT NULL DEFAULT TRUE"),
                    ("vault_enabled", "BOOLEAN NOT NULL DEFAULT TRUE"),
                    ("jackpot_enabled", "BOOLEAN NOT NULL DEFAULT TRUE"),
                ):
                    cur.execute(f"ALTER TABLE minigame_settings ADD COLUMN IF NOT EXISTS {col} {definition}")
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_minigame_settings_channel "
                    "ON minigame_settings(broadcaster_user_id, platform)"
                )
            conn.commit()
            _MINIGAME_SCHEMA_READY = True
        finally:
            conn.close()


def mark_minigame_schema_ready():
    """Chamado após init_db(): o boot já garantiu o schema e as migrações."""
    global _MINIGAME_SCHEMA_READY, _MINIGAME_RUNTIME_READY
    _MINIGAME_SCHEMA_READY = True
    _MINIGAME_RUNTIME_READY = True


def _invalidate_settings_cache(bid, platform=None):
    bid = int(bid)
    if platform is None:
        for key in [k for k in _SETTINGS_CACHE if k[0] == bid]:
            _SETTINGS_CACHE.pop(key, None)
    else:
        _SETTINGS_CACHE.pop((bid, _platform(platform)), None)


def get_settings(bid, platform="kick"):
    platform = _platform(platform)
    bid = int(bid)
    ensure_minigame_table()
    cache_key = (bid, platform)
    now = time.monotonic()
    cached = _SETTINGS_CACHE.get(cache_key)
    if cached and now - cached[0] < _SETTINGS_CACHE_TTL:
        return dict(cached[1])

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO minigame_settings (broadcaster_user_id, platform, last_slot_refill_at)
                VALUES (%s,%s,NOW())
                ON CONFLICT (broadcaster_user_id, platform) DO NOTHING
                """,
                (bid, platform),
            )
            cur.execute(
                """
                SELECT enabled, bets_enabled, slots_enabled, slot_bankroll, slot_bankroll_max, slot_hourly_refill,
                       slot_min_bet, slot_max_bet, slot_cooldown_seconds,
                       coinflip_enabled, polls_enabled, quiz_enabled, race_enabled, target_enabled, secret_enabled,
                       survival_enabled, steal_enabled, vault_enabled, jackpot_enabled,
                       last_slot_refill_at
                  FROM minigame_settings
                 WHERE broadcaster_user_id=%s AND platform=%s
                """,
                (bid, platform),
            )
            row = cur.fetchone()
        conn.commit()
    finally:
        conn.close()
    if not row:
        value = dict(DEFAULTS)
    else:
        value = _normalize({
            "enabled": row[0], "bets_enabled": row[1], "slots_enabled": row[2],
            "slot_bankroll": row[3], "slot_bankroll_max": row[4], "slot_hourly_refill": row[5],
            "slot_min_bet": row[6], "slot_max_bet": row[7], "slot_cooldown_seconds": row[8],
            "coinflip_enabled": row[9], "polls_enabled": row[10], "quiz_enabled": row[11],
            "race_enabled": row[12], "target_enabled": row[13], "secret_enabled": row[14],
            "survival_enabled": row[15], "steal_enabled": row[16], "vault_enabled": row[17],
            "jackpot_enabled": row[18],
        })
        value["last_slot_refill_at"] = row[19].isoformat() if row[19] else None
    _SETTINGS_CACHE[cache_key] = (now, dict(value))
    return value


def update_settings(bid, platform, values):
    platform = _platform(platform)
    ensure_minigame_table()
    current = get_settings(bid, platform)
    merged = dict(current)
    merged.update(values or {})
    clean = _normalize(merged)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO minigame_settings
                    (broadcaster_user_id,platform,enabled,bets_enabled,slots_enabled,slot_bankroll,slot_bankroll_max,
                     slot_hourly_refill,slot_min_bet,slot_max_bet,slot_cooldown_seconds,
                    coinflip_enabled,polls_enabled,quiz_enabled,race_enabled,target_enabled,secret_enabled,survival_enabled,steal_enabled,vault_enabled,jackpot_enabled,last_slot_refill_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                ON CONFLICT (broadcaster_user_id,platform) DO UPDATE SET
                    enabled=EXCLUDED.enabled,
                    bets_enabled=EXCLUDED.bets_enabled,
                    slots_enabled=EXCLUDED.slots_enabled,
                    slot_bankroll=EXCLUDED.slot_bankroll,
                    slot_bankroll_max=EXCLUDED.slot_bankroll_max,
                    slot_hourly_refill=EXCLUDED.slot_hourly_refill,
                    slot_min_bet=EXCLUDED.slot_min_bet,
                    slot_max_bet=EXCLUDED.slot_max_bet,
                    slot_cooldown_seconds=EXCLUDED.slot_cooldown_seconds,
                    coinflip_enabled=EXCLUDED.coinflip_enabled,polls_enabled=EXCLUDED.polls_enabled,quiz_enabled=EXCLUDED.quiz_enabled,
                    race_enabled=EXCLUDED.race_enabled,target_enabled=EXCLUDED.target_enabled,secret_enabled=EXCLUDED.secret_enabled,
                    survival_enabled=EXCLUDED.survival_enabled,steal_enabled=EXCLUDED.steal_enabled,vault_enabled=EXCLUDED.vault_enabled,jackpot_enabled=EXCLUDED.jackpot_enabled,
                    updated_at=NOW()
                """,
                (int(bid), platform, clean["enabled"], clean["bets_enabled"], clean["slots_enabled"],
                 clean["slot_bankroll"], clean["slot_bankroll_max"], clean["slot_hourly_refill"],
                 clean["slot_min_bet"], clean["slot_max_bet"], clean["slot_cooldown_seconds"],
                 clean["coinflip_enabled"],clean["polls_enabled"],clean["quiz_enabled"],clean["race_enabled"],clean["target_enabled"],clean["secret_enabled"],
                 clean["survival_enabled"],clean["steal_enabled"],clean["vault_enabled"],clean["jackpot_enabled"]),
            )
        conn.commit()
    finally:
        conn.close()
    _invalidate_settings_cache(bid, platform)
    return get_settings(bid, platform)


def update_minigame_enabled(bid, platform, game, enabled):
    platform = _platform(platform)
    game = str(game or "").strip().lower()
    field_by_game = {"bets": "bets_enabled", "slots": "slots_enabled", "coinflip": "coinflip_enabled", "polls": "polls_enabled", "quiz": "quiz_enabled", "race": "race_enabled", "target": "target_enabled", "secret": "secret_enabled", "survival": "survival_enabled", "steal": "steal_enabled", "vault": "vault_enabled", "jackpot": "jackpot_enabled"}
    field = field_by_game.get(game)
    if not field:
        raise ValueError("Mini Game inválido.")
    ensure_minigame_table()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO minigame_settings (broadcaster_user_id,platform,enabled,{field},last_slot_refill_at)
                VALUES (%s,%s,TRUE,%s,NOW())
                ON CONFLICT (broadcaster_user_id,platform) DO UPDATE SET
                    {field}=EXCLUDED.{field}, updated_at=NOW()
                """,
                (int(bid), platform, bool(enabled)),
            )
        conn.commit()
    finally:
        conn.close()
    _invalidate_settings_cache(bid, platform)
    return get_settings(bid, platform)

def _refill_locked(cur, bid, platform):
    cur.execute(
        """
        SELECT slot_bankroll, slot_bankroll_max, slot_hourly_refill, last_slot_refill_at
          FROM minigame_settings
         WHERE broadcaster_user_id=%s AND platform=%s
         FOR UPDATE
        """,
        (int(bid), platform),
    )
    row = cur.fetchone()
    if not row:
        return 0, 0, 0
    bankroll, max_bankroll, hourly, last = int(row[0]), int(row[1]), int(row[2]), row[3]
    if not last:
        cur.execute(
            "UPDATE minigame_settings SET last_slot_refill_at=NOW() WHERE broadcaster_user_id=%s AND platform=%s",
            (int(bid), platform),
        )
        return 0, bankroll, 0

    elapsed = max(0, (datetime.now(timezone.utc) - last).total_seconds())
    hours = int(elapsed // 3600)
    if hours <= 0 or hourly <= 0:
        return 0, bankroll, 0

    added = min(max(0, max_bankroll - bankroll), hours * hourly)
    cur.execute(
        """
        UPDATE minigame_settings
           SET slot_bankroll=LEAST(slot_bankroll_max,slot_bankroll+%s),
               last_slot_refill_at=NOW(), updated_at=NOW()
         WHERE broadcaster_user_id=%s AND platform=%s
        """,
        (int(added), int(bid), platform),
    )
    return int(added), min(max_bankroll, bankroll + added), hours




GAME_COOLDOWN = {}

def _game_allowed(bid, platform, game):
    settings = get_settings(bid, platform)
    return bool(settings.get("enabled", True) and settings.get(f"{game}_enabled", True))

def _points_player_locked(cur, bid, platform, username):
    cur.execute("SELECT points FROM players WHERE broadcaster_user_id=%s AND platform=%s AND username=%s FOR UPDATE", (int(bid), platform, username))
    row=cur.fetchone(); return int(row[0] or 0) if row else 0

def _adjust_points(bid, username, amount, platform, user_id=None):
    ensure_player(bid, username, user_id, platform)
    conn=get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE players SET points=GREATEST(0,points+%s),updated_at=NOW() WHERE broadcaster_user_id=%s AND platform=%s AND username=%s RETURNING points", (int(amount),int(bid),platform,username))
            row=cur.fetchone(); conn.commit(); return int(row[0] or 0) if row else 0
    finally: conn.close()

def play_coinflip(bid, username, choice, amount, platform="kick", user_id=None):
    if not _game_allowed(bid,platform,"coinflip"): return {"ok":False,"error":"🪙 Cara ou Coroa está desativado nesta plataforma."}
    choice=str(choice or '').lower(); choice='cara' if choice in {'cara','heads'} else 'coroa' if choice in {'coroa','tails'} else ''
    amount=int(amount)
    if not choice or amount<=0: return {"ok":False,"error":"🪙 Use !cara <valor> ou !coroa <valor>."}
    ensure_player(bid,username,user_id,platform); conn=get_conn()
    try:
        with conn.cursor() as cur:
            balance=_points_player_locked(cur,bid,platform,username)
            if balance<amount: conn.rollback(); return {"ok":False,"error":f"🪙 Saldo insuficiente. Você tem {balance} pontos."}
            result=random.choice(("cara","coroa")); payout=amount*2 if result==choice else 0
            cur.execute("UPDATE players SET points=points-%s+%s,updated_at=NOW() WHERE broadcaster_user_id=%s AND platform=%s AND username=%s RETURNING points",(amount,payout,int(bid),platform,username)); points=int(cur.fetchone()[0])
        conn.commit()
    finally: conn.close()
    forget_rankings(bid); return {"ok":True,"choice":choice,"result":result,"amount":amount,"payout":payout,"points":points}

def _game_table():
    """O schema do runtime já é criado no boot do banco."""
    global _MINIGAME_RUNTIME_READY
    if _MINIGAME_RUNTIME_READY:
        return
    with _MINIGAME_SCHEMA_LOCK:
        if _MINIGAME_RUNTIME_READY:
            return
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """CREATE TABLE IF NOT EXISTS minigame_runtime (
                        broadcaster_user_id BIGINT NOT NULL,
                        platform TEXT NOT NULL,
                        game TEXT NOT NULL,
                        state JSONB NOT NULL DEFAULT '{}'::jsonb,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        PRIMARY KEY(broadcaster_user_id,platform,game)
                    )"""
                )
            conn.commit()
            _MINIGAME_RUNTIME_READY = True
        finally:
            conn.close()


def _runtime_get(bid,platform,game,default=None):
    _game_table(); conn=get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT state FROM minigame_runtime WHERE broadcaster_user_id=%s AND platform=%s AND game=%s",(int(bid),platform,game)); row=cur.fetchone()
            return (row[0] if row and row[0] is not None else default) or {}
    finally: conn.close()

def _runtime_set(bid,platform,game,state):
    _game_table(); conn=get_conn()
    try:
        with conn.cursor() as cur: cur.execute("INSERT INTO minigame_runtime(broadcaster_user_id,platform,game,state) VALUES(%s,%s,%s,%s) ON CONFLICT(broadcaster_user_id,platform,game) DO UPDATE SET state=EXCLUDED.state,updated_at=NOW()",(int(bid),platform,game,state))
        conn.commit()
    finally: conn.close()

def start_poll(bid,username,question,options,platform="kick"):
    if not _game_allowed(bid,platform,"polls"): return {"ok":False,"error":"📊 Enquetes estão desativadas nesta plataforma."}
    options=[str(x).strip() for x in options if str(x).strip()][:5]
    if len(options)<2 or not question: return {"ok":False,"error":"📊 Use !enquete pergunta | opção 1 | opção 2."}
    state={"question":str(question)[:180],"options":options,"votes":{},"open":True,"started_at":time.time()}; _runtime_set(bid,platform,"poll",state); return {"ok":True,"state":state}

def vote_poll(bid,username,option,platform="kick"):
    state=_runtime_get(bid,platform,"poll",{})
    if not state.get("open"): return {"ok":False,"error":"📊 Não há enquete aberta."}
    options=state.get("options",[]); value=str(option).strip()
    idx=None
    for i,opt in enumerate(options,1):
        if value==str(i) or value.lower()==str(opt).lower(): idx=i
    if idx is None: return {"ok":False,"error":"📊 Opção inválida."}
    votes=state.setdefault("votes",{}); votes[username.lower()]=idx; _runtime_set(bid,platform,"poll",state); return {"ok":True,"option":options[idx-1]}

def close_poll(bid,platform="kick"):
    state=_runtime_get(bid,platform,"poll",{})
    if not state.get("open"): return {"ok":False,"error":"📊 Não há enquete aberta."}
    counts=[0]*len(state.get("options",[]))
    for v in state.get("votes",{}).values():
        if isinstance(v,int) and 1<=v<=len(counts): counts[v-1]+=1
    state["open"]=False; _runtime_set(bid,platform,"poll",state); return {"ok":True,"state":state,"counts":counts}

def _start_or_join_runtime(bid,username,platform,game,ttl=45):
    state=_runtime_get(bid,platform,game,{})
    now=time.time()
    if not state.get("open") or now-float(state.get("started_at",0))>ttl: state={"open":True,"started_at":now,"players":[]}
    if username.lower() not in [x.lower() for x in state["players"]]: state["players"].append(username)
    _runtime_set(bid,platform,game,state); return state

def race_join(bid,username,platform="kick"):
    if not _game_allowed(bid,platform,"race"): return {"ok":False,"error":"🏃 Corrida está desativada nesta plataforma."}
    return {"ok":True,"state":_start_or_join_runtime(bid,username,platform,"race",45)}

def race_finish(bid,platform="kick"):
    state=_runtime_get(bid,platform,"race",{}); players=state.get("players",[])
    if not players: return {"ok":False,"error":"🏃 Ninguém entrou na corrida."}
    random.shuffle(players); prizes=[500,300,150,75,30]; winners=[]
    for i,u in enumerate(players[:len(prizes)]): winners.append((u,prizes[i])); _adjust_points(bid,u,prizes[i],platform)
    state["open"]=False; _runtime_set(bid,platform,"race",state); forget_rankings(bid); return {"ok":True,"winners":winners}

def target_guess(bid,username,guess,platform="kick"):
    if not _game_allowed(bid,platform,"target"): return {"ok":False,"error":"🎯 Alvo está desativado nesta plataforma."}
    state=_runtime_get(bid,platform,"target",{})
    if not state.get("open"): state={"open":True,"target":random.randint(1,100)}; _runtime_set(bid,platform,"target",state)
    try: guess=int(guess)
    except: return {"ok":False,"error":"🎯 Use um número entre 1 e 100."}
    if not 1<=guess<=100: return {"ok":False,"error":"🎯 Use um número entre 1 e 100."}
    if guess==int(state["target"]): _adjust_points(bid,username,300,platform); state["open"]=False; _runtime_set(bid,platform,"target",state); forget_rankings(bid); return {"ok":True,"win":True,"points":300}
    return {"ok":True,"win":False,"distance":abs(guess-int(state["target"]))}

def secret_guess(bid,username,guess,platform="kick"):
    if not _game_allowed(bid,platform,"secret"): return {"ok":False,"error":"🔢 Número Secreto está desativado nesta plataforma."}
    state=_runtime_get(bid,platform,"secret",{})
    if not state.get("open"): state={"open":True,"target":random.randint(1,50)}; _runtime_set(bid,platform,"secret",state)
    try: guess=int(guess)
    except: return {"ok":False,"error":"🔢 Escolha um número entre 1 e 50."}
    if guess==int(state["target"]): _adjust_points(bid,username,500,platform); state["open"]=False; _runtime_set(bid,platform,"secret",state); forget_rankings(bid); return {"ok":True,"win":True,"points":500}
    return {"ok":True,"win":False,"hint":"maior" if guess<int(state["target"]) else "menor"}

def survival_join(bid,username,platform="kick"):
    if not _game_allowed(bid,platform,"survival"): return {"ok":False,"error":"🧟 Sobrevivência está desativada nesta plataforma."}
    return {"ok":True,"state":_start_or_join_runtime(bid,username,platform,"survival",30)}

def survival_finish(bid,platform="kick"):
    state=_runtime_get(bid,platform,"survival",{}); players=state.get("players",[])
    if not players: return {"ok":False,"error":"🧟 Ninguém entrou na sobrevivência."}
    survivors=[u for u in players if random.random()<0.35] or [random.choice(players)]
    winners=[]
    for u in survivors: _adjust_points(bid,u,250,platform); winners.append((u,250))
    state["open"]=False; _runtime_set(bid,platform,"survival",state); forget_rankings(bid); return {"ok":True,"winners":winners}

def steal_points(bid,username,target,platform="kick",user_id=None):
    if not _game_allowed(bid,platform,"steal"): return {"ok":False,"error":"💰 Roubo está desativado nesta plataforma."}
    if username.lower()==target.lower(): return {"ok":False,"error":"💰 Você não pode roubar de si mesmo."}
    ensure_player(bid,username,user_id,platform); conn=get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT username,points FROM players WHERE broadcaster_user_id=%s AND platform=%s AND username IN (%s,%s) FOR UPDATE",(int(bid),platform,username,target)); rows=cur.fetchall()
            if len(rows)<2: conn.rollback(); return {"ok":False,"error":"💰 Usuário não encontrado."}
            data={r[0].lower():(r[0],int(r[1])) for r in rows}; t=data.get(target.lower()); a=data.get(username.lower())
            if not t or t[1]<10: conn.rollback(); return {"ok":False,"error":"💰 O alvo não tem pontos suficientes."}
            if random.random()>0.22: conn.rollback(); return {"ok":True,"win":False}
            amount=max(1,min(int(t[1]*0.10),500)); cur.execute("UPDATE players SET points=points+%s WHERE broadcaster_user_id=%s AND platform=%s AND username=%s",(amount,int(bid),platform,username)); cur.execute("UPDATE players SET points=GREATEST(0,points-%s) WHERE broadcaster_user_id=%s AND platform=%s AND username=%s",(amount,int(bid),platform,t[0])); cur.execute("SELECT points FROM players WHERE broadcaster_user_id=%s AND platform=%s AND username=%s",(int(bid),platform,username)); newp=int(cur.fetchone()[0]); conn.commit()
    finally: conn.close()
    forget_rankings(bid); return {"ok":True,"win":True,"amount":amount,"points":newp}

def vault_play(bid,username,choice,platform="kick"):
    if not _game_allowed(bid,platform,"vault"): return {"ok":False,"error":"🔐 Cofre está desativado nesta plataforma."}
    try: choice=int(choice)
    except: return {"ok":False,"error":"🔐 Escolha uma combinação de 1 a 9."}
    if choice not in range(1,10): return {"ok":False,"error":"🔐 Escolha uma combinação de 1 a 9."}
    if random.random()<0.08: _adjust_points(bid,username,400,platform); return {"ok":True,"win":True,"points":400}
    return {"ok":True,"win":False}

def jackpot_play(bid,username,platform="kick",user_id=None):
    if not _game_allowed(bid,platform,"jackpot"): return {"ok":False,"error":"👑 Jackpot está desativado nesta plataforma."}
    ensure_player(bid,username,user_id,platform); conn=get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT slot_bankroll FROM minigame_settings WHERE broadcaster_user_id=%s AND platform=%s FOR UPDATE",(int(bid),platform)); row=cur.fetchone(); bank=int(row[0] or 0) if row else 0
            if bank<100: conn.rollback(); return {"ok":False,"error":"👑 O Jackpot ainda está acumulando pontos."}
            if random.random()>0.05: conn.rollback(); return {"ok":True,"win":False}
            prize=min(bank, max(100,bank//2)); cur.execute("UPDATE players SET points=points+%s WHERE broadcaster_user_id=%s AND platform=%s AND username=%s RETURNING points",(prize,int(bid),platform,username)); points=int(cur.fetchone()[0]); cur.execute("UPDATE minigame_settings SET slot_bankroll=slot_bankroll-%s WHERE broadcaster_user_id=%s AND platform=%s",(prize,int(bid),platform)); conn.commit()
    finally: conn.close()
    forget_rankings(bid); return {"ok":True,"win":True,"prize":prize,"points":points}

def play_slots(bid, username, amount, platform="kick", user_id=None):
    platform = _platform(platform)
    amount = int(amount)
    if amount <= 0:
        return {"ok": False, "error": "A aposta precisa ser maior que 0."}

    settings = get_settings(bid, platform)
    if not settings["enabled"] or not settings.get("slots_enabled", True):
        return {"ok": False, "error": "🎰 Os Slots estão desativados nesta plataforma."}
    if amount < settings["slot_min_bet"]:
        return {"ok": False, "error": f"🎰 A aposta mínima é {settings['slot_min_bet']} pontos."}
    if amount > settings["slot_max_bet"]:
        return {"ok": False, "error": f"🎰 A aposta máxima é {settings['slot_max_bet']} pontos."}

    key = (int(bid), platform, str(user_id or username).lower())
    now = time.monotonic()
    next_allowed = _COOLDOWN.get(key, 0)
    if now < next_allowed:
        remaining = max(1, int(next_allowed - now + 0.999))
        return {"ok": False, "error": f"🎰 Aguarde {remaining}s para jogar novamente."}

    ensure_player(bid, username, user_id, platform)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT points FROM players
                 WHERE broadcaster_user_id=%s AND platform=%s AND username=%s
                 FOR UPDATE
                """,
                (int(bid), platform, username),
            )
            player = cur.fetchone()
            balance = int(player[0] or 0) if player else 0
            if balance < amount:
                conn.rollback()
                return {"ok": False, "error": f"🎰 Você tem {balance} pontos e tentou apostar {amount}."}

            refill, bankroll, refill_hours = _refill_locked(cur, bid, platform)
            if bankroll < 0:
                conn.rollback()
                return {"ok": False, "error": "🎰 O cassino está indisponível no momento."}

            symbols, multiplier, outcome = _make_slots_roll()
            payout = int(amount * multiplier) if multiplier > 0 else 0
            if multiplier > 0 and payout < 1:
                payout = 1
            profit = payout - amount

            # O cassino nunca pode pagar mais lucro do que possui. Se o jackpot
            # estiver baixo, o resultado é convertido em perda para preservar a economia.
            if profit > bankroll:
                symbols, multiplier, payout, profit, outcome = ["💥", "💥", "💥"], 0.0, 0, -amount, "loss"

            cur.execute(
                """
                UPDATE players SET points=points-%s+%s, updated_at=NOW()
                 WHERE broadcaster_user_id=%s AND platform=%s AND username=%s
                RETURNING points
                """,
                (amount, payout, int(bid), platform, username),
            )
            new_points = int(cur.fetchone()[0])
            cur.execute(
                """
                UPDATE minigame_settings
                   SET slot_bankroll=GREATEST(0,slot_bankroll-%s+%s), updated_at=NOW()
                 WHERE broadcaster_user_id=%s AND platform=%s
                RETURNING slot_bankroll
                """,
                (max(0, profit), max(0, -profit), int(bid), platform),
            )
            house_after = int(cur.fetchone()[0])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    _COOLDOWN[key] = now + settings["slot_cooldown_seconds"]
    forget_rankings(bid)
    return {
        "ok": True,
        "symbols": " ".join(symbols),
        "symbol_list": symbols,
        "outcome": outcome,
        "multiplier": multiplier,
        "amount": amount,
        "payout": payout,
        "profit": profit,
        "points": new_points,
        "house": house_after,
        "refill": refill,
        "refill_hours": refill_hours,
        "currency": "Pontos",
    }
