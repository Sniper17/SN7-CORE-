import random
import time
from datetime import datetime, timezone

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
}

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
                    slot_max_bet BIGINT NOT NULL DEFAULT 1000,
                    slot_cooldown_seconds INTEGER NOT NULL DEFAULT 5,
                    last_slot_refill_at TIMESTAMPTZ,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (broadcaster_user_id, platform)
                )
                """
            )
            cur.execute("ALTER TABLE minigame_settings ADD COLUMN IF NOT EXISTS bets_enabled BOOLEAN NOT NULL DEFAULT TRUE")
            cur.execute("ALTER TABLE minigame_settings ADD COLUMN IF NOT EXISTS slots_enabled BOOLEAN NOT NULL DEFAULT TRUE")
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_minigame_settings_channel ON minigame_settings(broadcaster_user_id, platform)"
            )
        conn.commit()
    finally:
        conn.close()


def get_settings(bid, platform="kick"):
    platform = _platform(platform)
    ensure_minigame_table()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO minigame_settings (broadcaster_user_id, platform, last_slot_refill_at)
                VALUES (%s,%s,NOW())
                ON CONFLICT (broadcaster_user_id, platform) DO NOTHING
                """,
                (int(bid), platform),
            )
            cur.execute(
                """
                SELECT enabled, bets_enabled, slots_enabled, slot_bankroll, slot_bankroll_max, slot_hourly_refill,
                       slot_min_bet, slot_max_bet, slot_cooldown_seconds,
                       last_slot_refill_at
                  FROM minigame_settings
                 WHERE broadcaster_user_id=%s AND platform=%s
                """,
                (int(bid), platform),
            )
            row = cur.fetchone()
        conn.commit()
    finally:
        conn.close()
    if not row:
        return dict(DEFAULTS)
    value = _normalize({
        "enabled": row[0],
        "bets_enabled": row[1],
        "slots_enabled": row[2],
        "slot_bankroll": row[3],
        "slot_bankroll_max": row[4],
        "slot_hourly_refill": row[5],
        "slot_min_bet": row[6],
        "slot_max_bet": row[7],
        "slot_cooldown_seconds": row[8],
    })
    value["last_slot_refill_at"] = row[9].isoformat() if row[9] else None
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
                     slot_hourly_refill,slot_min_bet,slot_max_bet,slot_cooldown_seconds,last_slot_refill_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
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
                    updated_at=NOW()
                """,
                (int(bid), platform, clean["enabled"], clean["bets_enabled"], clean["slots_enabled"],
                 clean["slot_bankroll"], clean["slot_bankroll_max"], clean["slot_hourly_refill"],
                 clean["slot_min_bet"], clean["slot_max_bet"], clean["slot_cooldown_seconds"]),
            )
        conn.commit()
    finally:
        conn.close()
    return get_settings(bid, platform)


def update_minigame_enabled(bid, platform, game, enabled):
    platform = _platform(platform)
    game = str(game or "").strip().lower()
    field_by_game = {"bets": "bets_enabled", "slots": "slots_enabled"}
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
