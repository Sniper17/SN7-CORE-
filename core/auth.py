from flask import g, session
from core.database import get_conn


def get_session_broadcaster_id():
    # Evita duas consultas idênticas ao PostgreSQL no mesmo request
    # (por exemplo, ao renderizar / e /dashboard).
    if hasattr(g, "sn7_session_broadcaster_id"):
        return g.sn7_session_broadcaster_id

    raw = session.get("kick_broadcaster_id")
    if raw is None:
        g.sn7_session_broadcaster_id = None
        return None
    try:
        broadcaster_id = int(raw)
    except (TypeError, ValueError):
        g.sn7_session_broadcaster_id = None
        return None

    try:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM kick_connections WHERE broadcaster_user_id=%s LIMIT 1",
                    (broadcaster_id,),
                )
                if not cur.fetchone():
                    g.sn7_session_broadcaster_id = None
                    return None
        finally:
            conn.close()
    except Exception:
        g.sn7_session_broadcaster_id = None
        return None

    g.sn7_session_broadcaster_id = broadcaster_id
    return broadcaster_id


def require_session_broadcaster(broadcaster_id):
    current = get_session_broadcaster_id()
    if current is None:
        raise PermissionError("Nenhum canal Kick está conectado nesta sessão.")
    if int(current) != int(broadcaster_id):
        raise PermissionError("Acesso negado: este canal pertence a outro streamer.")
    return current
