"""Caches leves do SN7 Core para reduzir leituras repetidas do PostgreSQL.

Os caches são apenas de processo. O PostgreSQL continua sendo a fonte oficial
dos dados; reiniciar o Core não perde nenhuma informação.
"""
from collections import OrderedDict
from threading import RLock
import time

_PLAYER_TTL = 6 * 60 * 60
_PLAYER_MAX = 20000
_players = OrderedDict()
_players_lock = RLock()

_REWARD_TTL = 60
_rewards = {}
_rewards_lock = RLock()


def player_key(broadcaster_id, kick_user_id=None, username=None, platform="kick"):
    platform = str(platform or "kick").strip().lower()
    if kick_user_id is not None:
        try:
            return ("id", platform, int(broadcaster_id), int(kick_user_id))
        except (TypeError, ValueError):
            pass
    return ("name", platform, int(broadcaster_id), str(username or "").strip().lower())


def get_player_identity(broadcaster_id, kick_user_id=None, username=None, platform="kick"):
    key = player_key(broadcaster_id, kick_user_id, username, platform)
    now = time.monotonic()
    with _players_lock:
        item = _players.get(key)
        if not item:
            return None
        if item["expires_at"] <= now:
            _players.pop(key, None)
            return None
        _players.move_to_end(key)
        return dict(item)


def remember_player_identity(broadcaster_id, kick_user_id, username, platform="kick"):
    platform = str(platform or "kick").strip().lower()
    key = player_key(broadcaster_id, kick_user_id, username, platform)
    item = {
        "broadcaster_user_id": int(broadcaster_id),
        "kick_user_id": int(kick_user_id) if kick_user_id is not None else None,
        "username": str(username or "").strip(),
        "expires_at": time.monotonic() + _PLAYER_TTL,
    }
    with _players_lock:
        keys = [key]
        # Mantemos também uma chave por nick para as rotas antigas que ainda
        # recebem apenas username. O ID da Kick continua sendo a identidade.
        if item["username"]:
            keys.append(("name", platform, int(broadcaster_id), item["username"].lower()))
        for cache_key in keys:
            _players[cache_key] = item
            _players.move_to_end(cache_key)
        while len(_players) > _PLAYER_MAX:
            _players.popitem(last=False)


def forget_player(broadcaster_id, kick_user_id=None, username=None, platform="kick"):
    key = player_key(broadcaster_id, kick_user_id, username, platform)
    with _players_lock:
        _players.pop(key, None)


def get_cached_rewards(broadcaster_id):
    now = time.monotonic()
    bid = int(broadcaster_id)
    with _rewards_lock:
        item = _rewards.get(bid)
        if not item or item["expires_at"] <= now:
            _rewards.pop(bid, None)
            return None
        return dict(item["value"])


def set_cached_rewards(broadcaster_id, value):
    with _rewards_lock:
        _rewards[int(broadcaster_id)] = {
            "value": dict(value),
            "expires_at": time.monotonic() + _REWARD_TTL,
        }


def forget_rewards(broadcaster_id):
    with _rewards_lock:
        _rewards.pop(int(broadcaster_id), None)


_COMMAND_TTL = 300  # configurações mudam via painel e invalidam o cache imediatamente
_commands = {}
_commands_lock = RLock()


def get_cached_commands(broadcaster_id):
    now = time.monotonic()
    bid = int(broadcaster_id)
    with _commands_lock:
        item = _commands.get(bid)
        if not item or item["expires_at"] <= now:
            _commands.pop(bid, None)
            return None
        return [dict(x) for x in item["value"]]


def set_cached_commands(broadcaster_id, value):
    with _commands_lock:
        _commands[int(broadcaster_id)] = {
            "value": [dict(x) for x in value],
            "expires_at": time.monotonic() + _COMMAND_TTL,
        }


def forget_commands(broadcaster_id):
    with _commands_lock:
        _commands.pop(int(broadcaster_id), None)


_CHANNEL_TTL = 60
_channels = {}
_channels_lock = RLock()


def get_cached_channel(broadcaster_id):
    now = time.monotonic()
    bid = int(broadcaster_id)
    with _channels_lock:
        item = _channels.get(bid)
        if not item or item["expires_at"] <= now:
            _channels.pop(bid, None)
            return None
        return dict(item["value"])


def set_cached_channel(broadcaster_id, value):
    with _channels_lock:
        _channels[int(broadcaster_id)] = {
            "value": dict(value),
            "expires_at": time.monotonic() + _CHANNEL_TTL,
        }


def forget_channel(broadcaster_id):
    with _channels_lock:
        _channels.pop(int(broadcaster_id), None)


# Cache curtíssimo para !pontos. Ele evita abrir uma nova conexão/consulta ao
# PostgreSQL quando o mesmo usuário consulta o saldo repetidamente. O TTL é
# intencionalmente baixo para não esconder alterações de pontos.
_BALANCE_TTL = 0.75
_BALANCE_MAX = 20000
_balances = OrderedDict()
_balances_lock = RLock()

def _balance_key(broadcaster_id, platform, username):
    return (int(broadcaster_id), str(platform or "kick").strip().lower(), str(username or "").strip().lower())

def get_cached_balance(broadcaster_id, platform="kick", username=""):
    now = time.monotonic()
    key = _balance_key(broadcaster_id, platform, username)
    with _balances_lock:
        item = _balances.get(key)
        if not item or item["expires_at"] <= now:
            _balances.pop(key, None)
            return None
        _balances.move_to_end(key)
        return dict(item["value"])

def set_cached_balance(broadcaster_id, value, platform="kick", username=""):
    key = _balance_key(broadcaster_id, platform, username)
    with _balances_lock:
        _balances[key] = {
            "value": dict(value),
            "expires_at": time.monotonic() + _BALANCE_TTL,
        }
        _balances.move_to_end(key)
        while len(_balances) > _BALANCE_MAX:
            _balances.popitem(last=False)

def forget_balance(broadcaster_id, platform="kick", username=""):
    key = _balance_key(broadcaster_id, platform, username)
    with _balances_lock:
        _balances.pop(key, None)

def forget_balances(broadcaster_id):
    bid = int(broadcaster_id)
    with _balances_lock:
        for key in [key for key in _balances if key[0] == bid]:
            _balances.pop(key, None)

# Cache curto dos rankings para o painel e comandos. A gravação de pontos
# invalida este cache, então o painel não fica preso em dados antigos.
_RANKING_TTL = 1.0
_rankings = {}
_rankings_lock = RLock()


def _ranking_key(broadcaster_id, platform, limit, all_platforms=False):
    return (int(broadcaster_id), str(platform or "kick").strip().lower(), int(limit), bool(all_platforms))


def get_cached_ranking(broadcaster_id, platform="kick", limit=50, all_platforms=False):
    now = time.monotonic()
    key = _ranking_key(broadcaster_id, platform, limit, all_platforms)
    with _rankings_lock:
        item = _rankings.get(key)
        if not item or item["expires_at"] <= now:
            _rankings.pop(key, None)
            return None
        return item["value"]


def set_cached_ranking(broadcaster_id, value, platform="kick", limit=50, all_platforms=False):
    key = _ranking_key(broadcaster_id, platform, limit, all_platforms)
    with _rankings_lock:
        _rankings[key] = {"value": value, "expires_at": time.monotonic() + _RANKING_TTL}


def forget_rankings(broadcaster_id):
    bid = int(broadcaster_id)
    with _rankings_lock:
        for key in [key for key in _rankings if key[0] == bid]:
            _rankings.pop(key, None)
