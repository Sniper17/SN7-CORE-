import hashlib
import time
import threading

from flask import g, session
from core.database import get_conn

_AUTH_CACHE_TTL = 20.0
_auth_cache = {}
_auth_cache_lock = threading.RLock()


def stable_channel_id(provider, external_user_id):
    """Gera um ID numérico estável para uma sessão que começou fora da Kick."""
    raw = f"sn7:{str(provider).strip().lower()}:{str(external_user_id).strip()}".encode("utf-8")
    value = int.from_bytes(hashlib.sha256(raw).digest()[:8], "big") & ((1 << 62) - 1)
    return value or 1


def get_session_broadcaster_id(validate=True):
    """Obtém o canal da sessão, aceitando Kick, Twitch ou YouTube.

    Mantém kick_broadcaster_id por compatibilidade com versões antigas, mas
    também aceita sn7_broadcaster_id para logins iniciados por Twitch/YouTube.
    """
    if not validate:
        raw = session.get("sn7_broadcaster_id", session.get("kick_broadcaster_id"))
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    if hasattr(g, "sn7_session_broadcaster_id_validated"):
        return g.sn7_session_broadcaster_id_validated

    raw = session.get("sn7_broadcaster_id", session.get("kick_broadcaster_id"))
    if raw is None:
        g.sn7_session_broadcaster_id_validated = None
        return None

    try:
        broadcaster_id = int(raw)
    except (TypeError, ValueError):
        g.sn7_session_broadcaster_id_validated = None
        return None

    cache_key = int(broadcaster_id)
    now = time.monotonic()
    with _auth_cache_lock:
        cached_at = _auth_cache.get(cache_key)
        if cached_at and now - cached_at < _AUTH_CACHE_TTL:
            g.sn7_session_broadcaster_id_validated = broadcaster_id
            return broadcaster_id

    try:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT 1
                      FROM kick_connections
                     WHERE broadcaster_user_id=%s OR sn7_profile_id=%s
                    UNION ALL
                    SELECT 1
                      FROM chat_connections
                     WHERE broadcaster_user_id=%s
                     LIMIT 1
                    """,
                    (broadcaster_id, broadcaster_id, broadcaster_id),
                )
                if not cur.fetchone():
                    g.sn7_session_broadcaster_id_validated = None
                    return None
        finally:
            conn.close()
    except Exception as exc:
        # A sessão Flask é assinada pelo FLASK_SECRET_KEY. Uma falha
        # transitória do PostgreSQL não deve transformar uma sessão válida em
        # "deslogada" nem produzir 403 falsos no painel.
        print(f"[AUTH] validação do canal adiada por falha no banco: {exc}", flush=True)
        with _auth_cache_lock:
            _auth_cache[cache_key] = now
        g.sn7_session_broadcaster_id_validated = broadcaster_id
        return broadcaster_id

    with _auth_cache_lock:
        _auth_cache[cache_key] = now
    g.sn7_session_broadcaster_id_validated = broadcaster_id
    return broadcaster_id


def require_session_broadcaster(broadcaster_id):
    current = get_session_broadcaster_id()
    if current is None:
        raise PermissionError("Nenhuma conta está conectada nesta sessão.")
    if int(current) != int(broadcaster_id):
        raise PermissionError("Acesso negado: este canal pertence a outro streamer.")
    return current
