from urllib.parse import urlparse
import time

from core.database import get_conn


def _provider(url):
    if not url:
        return 'unknown'
    host = (urlparse(url).netloc or '').lower()
    if 'youtube.com' in host or 'youtu.be' in host:
        return 'youtube'
    if 'spotify.com' in host:
        return 'spotify'
    if 'soundcloud.com' in host:
        return 'soundcloud'
    return 'link'


PLAYABLE_PROVIDERS = {'youtube', 'link'}
DIRECT_AUDIO_EXTENSIONS = ('.mp3', '.m4a', '.aac', '.ogg', '.wav', '.opus')


def _is_direct_audio_url(url):
    path = urlparse(str(url or '').strip()).path.lower()
    return path.endswith(DIRECT_AUDIO_EXTENSIONS)


def add_from_chat(bid, query, user):
    query = str(query or '').strip()
    if not query:
        raise ValueError('Informe uma música ou link.')
    provider = _provider(query if query.startswith(('http://', 'https://')) else '')
    source_url = query if provider != 'unknown' else ''
    if provider not in PLAYABLE_PROVIDERS:
        raise ValueError('Esta fonte ainda não é compatível com o player do OBS. Use um link do YouTube ou um link direto de áudio.')
    if provider == 'link' and not _is_direct_audio_url(source_url):
        raise ValueError('Para links, use uma URL direta de áudio (.mp3, .m4a, .aac, .ogg, .wav ou .opus).')
    title = query
    artist = ''
    if not source_url and ' - ' in query:
        artist, title = [part.strip() for part in query.split(' - ', 1)]
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT allow_youtube,allow_spotify,allow_soundcloud,allow_links FROM music_settings WHERE broadcaster_user_id=%s', (int(bid),))
            settings = cur.fetchone() or (True, True, False, True)
            if provider == 'youtube' and not settings[0]: raise ValueError('YouTube está desativado para este canal.')
            if provider == 'spotify' and not settings[1]: raise ValueError('Spotify está desativado para este canal.')
            if provider == 'soundcloud' and not settings[2]: raise ValueError('SoundCloud está desativado para este canal.')
            if source_url and not settings[3]: raise ValueError('Links estão desativados para este canal.')
            cur.execute("SELECT COUNT(*) FROM music_queue WHERE broadcaster_user_id=%s AND status='queued'", (int(bid),))
            queued_count = int(cur.fetchone()[0] or 0)
            if queued_count >= 100:
                raise ValueError('A fila deste canal já atingiu o limite de 100 músicas.')
            cur.execute("SELECT COALESCE(MAX(position),0)+1 FROM music_queue WHERE broadcaster_user_id=%s AND status='queued'", (int(bid),))
            position = int(cur.fetchone()[0] or 1)
            cur.execute("INSERT INTO music_queue(broadcaster_user_id,provider,title,artist,source_url,added_by,status,position) VALUES(%s,%s,%s,%s,%s,%s,'queued',%s) RETURNING id", (int(bid), provider, title[:200], artist[:160], source_url[:1000], str(user)[:80], position))
            item_id = cur.fetchone()[0]
            cur.execute("INSERT INTO music_player_state(broadcaster_user_id,current_queue_id) VALUES(%s,%s) ON CONFLICT (broadcaster_user_id) DO UPDATE SET current_queue_id=COALESCE(music_player_state.current_queue_id,EXCLUDED.current_queue_id), updated_at=NOW()", (int(bid), item_id))
        conn.commit()
        return {'id': item_id, 'title': title[:200], 'artist': artist[:160], 'provider': provider}, position
    finally:
        conn.close()


def current_and_queue(bid):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT s.current_queue_id,q.id,q.title,q.artist,q.provider,q.source_url FROM music_player_state s LEFT JOIN music_queue q ON q.id=s.current_queue_id AND q.status=\'queued\' WHERE s.broadcaster_user_id=%s', (int(bid),))
            row = cur.fetchone()
            current = {'id': row[1], 'title': row[2], 'artist': row[3], 'provider': row[4], 'source_url': row[5] or ''} if row and row[1] else None
            cur.execute("SELECT id,title,artist,provider FROM music_queue WHERE broadcaster_user_id=%s AND status='queued' AND id<>COALESCE(%s,0) ORDER BY position,id LIMIT 20", (int(bid), row[0] if row else None))
            queue = [{'id': r[0], 'title': r[1], 'artist': r[2], 'provider': r[3]} for r in cur.fetchall()]
            return current, queue
    finally:
        conn.close()


def set_playing(bid, playing):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO music_player_state(broadcaster_user_id,is_playing) VALUES(%s,%s) ON CONFLICT(broadcaster_user_id) DO UPDATE SET is_playing=EXCLUDED.is_playing,updated_at=NOW()", (int(bid), bool(playing)))
        conn.commit()
    finally:
        conn.close()


def skip_current(bid):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT current_queue_id FROM music_player_state WHERE broadcaster_user_id=%s FOR UPDATE', (int(bid),))
            row = cur.fetchone()
            current_id = row[0] if row else None
            if current_id:
                cur.execute("UPDATE music_queue SET status='played' WHERE id=%s AND broadcaster_user_id=%s", (current_id, int(bid)))
            cur.execute("SELECT id,title,artist,provider FROM music_queue WHERE broadcaster_user_id=%s AND status='queued' ORDER BY position,id LIMIT 1", (int(bid),))
            nxt = cur.fetchone()
            next_id = nxt[0] if nxt else None
            cur.execute('UPDATE music_player_state SET current_queue_id=%s,is_playing=%s,updated_at=NOW() WHERE broadcaster_user_id=%s', (next_id, bool(next_id), int(bid)))
        conn.commit()
        return {'id': nxt[0], 'title': nxt[1], 'artist': nxt[2], 'provider': nxt[3]} if nxt else None
    finally:
        conn.close()


def clear_queue(bid):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM music_queue WHERE broadcaster_user_id=%s AND status='queued'", (int(bid),))
            cur.execute('UPDATE music_player_state SET current_queue_id=NULL,is_playing=FALSE,updated_at=NOW() WHERE broadcaster_user_id=%s', (int(bid),))
        conn.commit()
    finally:
        conn.close()


_public_commands_cache = {}
_PUBLIC_COMMANDS_CACHE_TTL = 30


def public_commands_enabled(bid):
    bid = int(bid)
    now = time.time()
    cached = _public_commands_cache.get(bid)
    if cached and now - cached[0] < _PUBLIC_COMMANDS_CACHE_TTL:
        return bool(cached[1])

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT public_commands FROM music_settings WHERE broadcaster_user_id=%s",
                (bid,),
            )
            row = cur.fetchone()
            value = bool(row[0]) if row else False
            _public_commands_cache[bid] = (now, value)
            return value
    finally:
        conn.close()


def set_public_commands_cache(bid, value):
    _public_commands_cache[int(bid)] = (time.time(), bool(value))
