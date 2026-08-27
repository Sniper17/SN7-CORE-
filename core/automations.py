import threading
import time
from datetime import datetime, timezone
from core.database import get_conn

PLATFORMS={"kick","twitch","youtube"}
_worker_started=False
_lock=threading.Lock()

def ensure_table():
    conn=get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('''CREATE TABLE IF NOT EXISTS chat_automations (
                id BIGSERIAL PRIMARY KEY,
                broadcaster_user_id BIGINT NOT NULL,
                platform TEXT NOT NULL DEFAULT 'kick',
                name TEXT NOT NULL,
                message TEXT NOT NULL,
                interval_seconds INTEGER NOT NULL DEFAULT 1800,
                enabled BOOLEAN NOT NULL DEFAULT TRUE,
                only_when_live BOOLEAN NOT NULL DEFAULT TRUE,
                last_sent_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )''')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_chat_automations_due ON chat_automations(enabled,platform,last_sent_at)')
        conn.commit()
    finally: conn.close()

def list_automations(bid):
    ensure_table(); conn=get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT id,name,message,platform,interval_seconds,enabled,only_when_live,last_sent_at FROM chat_automations WHERE broadcaster_user_id=%s ORDER BY id DESC',(int(bid),))
            rows=cur.fetchall()
        return [{"id":r[0],"name":r[1],"message":r[2],"platform":r[3],"interval_seconds":r[4],"enabled":bool(r[5]),"only_when_live":bool(r[6]),"last_sent_at":r[7].isoformat() if r[7] else None} for r in rows]
    finally: conn.close()

def save_automation(bid,data,automation_id=None):
    ensure_table(); platform=str(data.get('platform') or 'kick').lower()
    if platform not in PLATFORMS: raise ValueError('Plataforma inválida.')
    name=str(data.get('name') or 'Mensagem automática').strip()[:80]
    message=str(data.get('message') or '').strip()[:500]
    if not message: raise ValueError('Digite uma mensagem.')
    interval=max(5,min(86400,int(data.get('interval_seconds') or 1800)))
    enabled=bool(data.get('enabled',True)); live=bool(data.get('only_when_live',True))
    conn=get_conn()
    try:
        with conn.cursor() as cur:
            if automation_id:
                cur.execute('UPDATE chat_automations SET name=%s,message=%s,platform=%s,interval_seconds=%s,enabled=%s,only_when_live=%s,updated_at=NOW() WHERE id=%s AND broadcaster_user_id=%s RETURNING id',(name,message,platform,interval,enabled,live,int(automation_id),int(bid)))
            else:
                cur.execute('INSERT INTO chat_automations(broadcaster_user_id,platform,name,message,interval_seconds,enabled,only_when_live) VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING id',(int(bid),platform,name,message,interval,enabled,live))
            row=cur.fetchone()
        conn.commit()
    finally: conn.close()
    return row[0] if row else None

def delete_automation(bid,automation_id):
    ensure_table(); conn=get_conn()
    try:
        with conn.cursor() as cur: cur.execute('DELETE FROM chat_automations WHERE id=%s AND broadcaster_user_id=%s',(int(automation_id),int(bid))); n=cur.rowcount
        conn.commit(); return n
    finally: conn.close()

def _is_live(platform,bid):
    try:
        if platform=='kick':
            from routes.kick import _kick_channel_is_live
            return _kick_channel_is_live(bid)
        if platform=='youtube':
            from routes.youtube import _get_connection, _find_live_chat
            conn=_get_connection(bid)
            return bool(conn and conn.get('bot_active') and _find_live_chat(conn))
        # Twitch: bot connection is the safe availability signal; EventSub is
        # already tied to the channel chat. Avoid OAuth changes or extra scopes.
        from routes.twitch import _conn
        conn=_conn(bid)
        return bool(conn and conn.get('bot_active'))
    except Exception as exc:
        print(f'[AUTOMATION] live check failed: {exc}',flush=True); return False

def _send(platform,bid,message):
    if platform=='kick':
        from routes.kick import _send_chat
        return _send_chat(bid,message)
    if platform=='twitch':
        from routes.twitch import _refresh,_conn,_send_chat
        conn=_refresh(_conn(bid))
        if not conn or not conn.get('bot_active'): return False
        _send_chat(conn,message); return True
    from routes.youtube import _get_connection,_find_live_chat,_send
    conn=_get_connection(bid)
    if not conn or not conn.get('bot_active'): return False
    chat=_find_live_chat(conn)
    if not chat: return False
    conn=dict(conn); conn['_chat_id']=chat; _send(conn,message); return True

def _worker():
    try:
        ensure_table()
    except Exception as exc:
        print(f'[AUTOMATION] initial table setup failed: {exc}', flush=True)
    while True:
        try:
            conn=get_conn()
            due=[]
            try:
                with conn.cursor() as cur:
                    cur.execute('''SELECT id,broadcaster_user_id,platform,message,interval_seconds,only_when_live
                                   FROM chat_automations WHERE enabled=TRUE AND (last_sent_at IS NULL OR last_sent_at <= NOW() - (interval_seconds * INTERVAL '1 second'))
                                   ORDER BY id LIMIT 50 FOR UPDATE SKIP LOCKED''')
                    due=cur.fetchall()
                    for row in due:
                        cur.execute('UPDATE chat_automations SET last_sent_at=NOW(),updated_at=NOW() WHERE id=%s',(row[0],))
                conn.commit()
            finally: conn.close()
            for aid,bid,platform,message,interval,only_live in due:
                if only_live and not _is_live(platform,bid):
                    # Give it another short chance instead of consuming the full interval.
                    conn=get_conn()
                    try:
                        with conn.cursor() as cur: cur.execute('UPDATE chat_automations SET last_sent_at=NOW()-((interval_seconds-5)*INTERVAL \'1 second\') WHERE id=%s',(aid,))
                        conn.commit()
                    finally: conn.close()
                    continue
                try: _send(platform,bid,message)
                except Exception as exc: print(f'[AUTOMATION] send failed id={aid}: {exc}',flush=True)
        except Exception as exc:
            print(f'[AUTOMATION] worker error: {exc}',flush=True)
        time.sleep(5)

def start_worker():
    global _worker_started
    with _lock:
        if _worker_started: return
        _worker_started=True
        threading.Thread(target=_worker,name='sn7-automations',daemon=True).start()
