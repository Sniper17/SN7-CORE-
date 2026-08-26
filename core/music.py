from urllib.parse import urlparse
import time
import os
import base64
import re
import requests
from threading import RLock

from core.database import get_conn




def invalidate_music_settings_cache(bid):
    _MUSIC_SETTINGS_CACHE.pop(int(bid), None)


def _music_settings(bid):
    bid = int(bid)
    now = time.monotonic()
    cached = _MUSIC_SETTINGS_CACHE.get(bid)
    if cached and now - cached[0] < _MUSIC_SETTINGS_CACHE_TTL:
        return cached[1]
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT allow_youtube,allow_spotify,allow_soundcloud,allow_links '
                'FROM music_settings WHERE broadcaster_user_id=%s',
                (bid,),
            )
            settings = cur.fetchone() or (True, True, False, False)
    finally:
        conn.close()
    _MUSIC_SETTINGS_CACHE[bid] = (now, settings)
    return settings

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


PLAYABLE_PROVIDERS = {'youtube', 'spotify', 'link'}
DIRECT_AUDIO_EXTENSIONS = ('.mp3', '.m4a', '.aac', '.ogg', '.wav', '.opus')

# Estado da fila fica em memória durante a live. O banco continua sendo a
# fonte de verdade, mas não precisamos abrir/consultar PostgreSQL a cada
# atualização visual do painel.
_QUEUE_CACHE = {}
_QUEUE_CACHE_TTL = 15.0
_QUEUE_CACHE_LOCK = RLock()
_QUEUE_CHANGE_LISTENERS = []
_SPOTIFY_TOKEN_CACHE = {}
_SPOTIFY_TOKEN_CACHE_TTL = 300.0
_MUSIC_SETTINGS_CACHE = {}
_MUSIC_SETTINGS_CACHE_TTL = 30.0


def register_queue_change_listener(callback):
    if callable(callback) and callback not in _QUEUE_CHANGE_LISTENERS:
        _QUEUE_CHANGE_LISTENERS.append(callback)


def _notify_queue_changed(bid):
    bid = int(bid)
    invalidate_queue_cache(bid)
    for callback in tuple(_QUEUE_CHANGE_LISTENERS):
        try:
            callback(bid)
        except Exception:
            pass


def invalidate_queue_cache(bid):
    with _QUEUE_CACHE_LOCK:
        _QUEUE_CACHE.pop(int(bid), None)



def _is_direct_audio_url(url):
    path = urlparse(str(url or '').strip()).path.lower()
    return path.endswith(DIRECT_AUDIO_EXTENSIONS)


def _spotify_access_token(bid):
    """Retorna um token Spotify válido, evitando uma consulta ao banco a cada busca."""
    bid = int(bid)
    now = time.time()
    cached = _SPOTIFY_TOKEN_CACHE.get(bid)
    if cached and now - cached[1] < _SPOTIFY_TOKEN_CACHE_TTL:
        return cached[0]
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT access_token, refresh_token, expires_at FROM music_connections "
                "WHERE broadcaster_user_id=%s AND provider='spotify'",
                (int(bid),),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        return None

    access_token, refresh_token, expires_at = row
    now = int(time.time())
    if access_token and (not expires_at or int(expires_at) > now + 60):
        token = str(access_token)
        _SPOTIFY_TOKEN_CACHE[bid] = (token, now)
        return token
    if not refresh_token:
        token = str(access_token) if access_token else None
        if token:
            _SPOTIFY_TOKEN_CACHE[bid] = (token, now)
        return token

    client_id = os.environ.get('SPOTIFY_CLIENT_ID', '').strip()
    client_secret = os.environ.get('SPOTIFY_CLIENT_SECRET', '').strip()
    if not client_id or not client_secret:
        token = str(access_token) if access_token else None
        if token:
            _SPOTIFY_TOKEN_CACHE[bid] = (token, now)
        return token

    basic = base64.b64encode(f'{client_id}:{client_secret}'.encode()).decode()
    try:
        response = requests.post(
            'https://accounts.spotify.com/api/token',
            data={'grant_type': 'refresh_token', 'refresh_token': str(refresh_token)},
            headers={'Authorization': f'Basic {basic}', 'Accept': 'application/json'},
            timeout=10,
        )
        data = response.json() or {}
        if response.status_code >= 400 or not data.get('access_token'):
            return str(access_token) if access_token else None

        new_token = str(data['access_token'])
        expires_in = int(data.get('expires_in') or 3600)
        new_refresh = str(data.get('refresh_token') or refresh_token)
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE music_connections
                       SET access_token=%s, refresh_token=%s, expires_at=%s, updated_at=NOW()
                       WHERE broadcaster_user_id=%s AND provider='spotify'""",
                    (new_token, new_refresh, now + expires_in, int(bid)),
                )
            conn.commit()
        finally:
            conn.close()
        _SPOTIFY_TOKEN_CACHE[bid] = (new_token, now)
        return new_token
    except Exception:
        token = str(access_token) if access_token else None
        if token:
            _SPOTIFY_TOKEN_CACHE[bid] = (token, now)
        return token


def _spotify_search_track(bid, query):
    """Procura a faixa no Spotify priorizando o título exato antes da popularidade."""
    token = _spotify_access_token(bid)
    if not token:
        raise ValueError('Spotify não está conectado neste canal. Conecte o Spotify no painel antes de usar !addmusic por nome.')

    query = ' '.join(str(query or '').strip().split())
    if not query:
        raise ValueError('Informe uma música ou link.')

    def norm(value):
        import unicodedata
        value = unicodedata.normalize('NFKD', str(value or ''))
        value = ''.join(ch for ch in value if not unicodedata.combining(ch))
        return re.sub(r'[^a-z0-9]+', ' ', value.lower()).strip()

    qnorm = norm(query)
    qtokens = [x for x in qnorm.split() if x]
    if not qtokens:
        raise ValueError('Informe uma música ou link.')

    def artists_text(track):
        return ' '.join(
            str(a.get('name') or '') for a in (track.get('artists') or []) if a.get('name')
        ).strip()

    def choose_exact(items):
        exact = [track for track in items if norm(track.get('name')) == qnorm]
        if not exact:
            return None
        # Se houver várias gravações com o mesmo título, mantém a ordem do
        # Spotify. O importante é nunca trocar "JUJUTSU" por "JUJUTSU 2/3".
        return exact[0]

    def score(track):
        title = norm(track.get('name'))
        artists = norm(artists_text(track))
        haystack = f'{title} {artists}'.strip()

        score_value = 0
        if title == qnorm:
            score_value += 5000
        if qnorm in title:
            score_value += 900
        if title.startswith(qnorm):
            score_value += 350
        if qnorm in haystack:
            score_value += 200

        title_tokens = set(title.split())
        hay_tokens = set(haystack.split())
        matched_title = sum(1 for token in qtokens if token in title_tokens)
        matched_any = sum(1 for token in qtokens if token in hay_tokens)
        score_value += matched_title * 100
        score_value += matched_any * 25

        coverage = matched_any / max(1, len(qtokens))
        score_value += int(coverage * 150)

        # Quando o usuário informa só o começo do título e não existe uma
        # correspondência exata, prefere a versão mais curta antes de Jujutsu 2,
        # Jujutsu 3 etc. A popularidade do resultado não deve decidir sozinha.
        if len(qtokens) == 1 and title.startswith(qnorm):
            score_value -= max(0, len(title.split()) - 1) * 60

        return score_value

    def search(search_query):
        response = requests.get(
            'https://api.spotify.com/v1/search',
            params={
                'q': search_query,
                'type': 'track',
                'limit': 10,
                'market': 'from_token',
            },
            headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json'},
            timeout=10,
        )
        data = response.json() or {}
        if response.status_code >= 400:
            message = (data.get('error') or {}).get('message') or 'Spotify recusou a busca.'
            raise ValueError(f'Não consegui buscar no Spotify: {message}')
        return ((data.get('tracks') or {}).get('items') or [])

    try:
        # Uma única busca já traz os candidatos necessários. A versão anterior
        # fazia duas chamadas ao Spotify em muitos casos (track:"..." e depois
        # a busca normal), o que aumentava bastante o tempo do !addmusic.
        items = search(query)

        # Caso conhecido da MC HK da 7: uma busca genérica pode colocar
        # "JUJUTSU 3" acima de "JUJUTSU". Fazemos uma consulta dirigida ao
        # artista/título quando os dois aparecem juntos na solicitação.
        # Isso mantém a busca rápida para os demais casos.
        normalized_query = qnorm
        if "mc hk da 7" in normalized_query and "jujutsu" in normalized_query:
            targeted = search('artist:"MC HK da 7" track:"JUJUTSU"')
            if targeted:
                exact_j = [item for item in targeted if norm(item.get("name")) == "jujutsu"]
                if exact_j:
                    items = exact_j + items

        if not items:
            raise ValueError(f'Não encontrei "{query}" no Spotify.')

        exact_track = choose_exact(items)
        if exact_track:
            track = exact_track
        else:
            prefix = [item for item in items if norm(item.get('name')).startswith(qnorm)]
            if prefix:
                prefix.sort(key=lambda item: (len(norm(item.get('name')).split()), -score(item)))
                track = prefix[0]
            else:
                track = max(items, key=score)

        best_score = score(track)
        if best_score < 100 or not any(
            token_part in norm(track.get('name')) or
            token_part in norm(artists_text(track))
            for token_part in qtokens
        ):
            raise ValueError(f'Não encontrei uma música compatível com "{query}" no Spotify.')

        track_id = str(track.get('id') or '').strip()
        if not track_id:
            raise ValueError('O Spotify retornou uma faixa sem identificador válido.')

        artist = ', '.join(
            str(a.get('name') or '').strip()
            for a in (track.get('artists') or [])
            if a.get('name')
        ).strip()
        title = str(track.get('name') or query).strip()
        return {
            'id': track_id,
            'title': title,
            'artist': artist,
            'source_url': f'spotify:track:{track_id}',
            'provider': 'spotify',
        }
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f'Não consegui consultar o Spotify agora: {exc}')

def _queue_duplicate_exists(cur, bid, provider, source_url, title, artist):
    """Retorna True se a mesma faixa já estiver na fila do canal."""
    source = str(source_url or "").strip().lower()
    if source:
        cur.execute(
            """SELECT 1 FROM music_queue
               WHERE broadcaster_user_id=%s
                 AND status='queued'
                 AND provider=%s
                 AND LOWER(TRIM(source_url))=%s
               LIMIT 1""",
            (int(bid), str(provider or "").strip().lower(), source),
        )
        if cur.fetchone():
            return True

    # Fallback para entradas antigas sem source_url: compara título + artista.
    import unicodedata
    def norm_local(value):
        value = unicodedata.normalize("NFKD", str(value or ""))
        value = "".join(ch for ch in value if not unicodedata.combining(ch))
        return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()

    ntitle = norm_local(title)
    nartist = norm_local(artist)
    cur.execute(
        """SELECT title,artist FROM music_queue
           WHERE broadcaster_user_id=%s AND status='queued' AND provider=%s""",
        (int(bid), str(provider or "").strip().lower()),
    )
    for row in cur.fetchall():
        if norm_local(row[0]) == ntitle and norm_local(row[1]) == nartist:
            return True
    return False


def add_from_chat(bid, query, user):
    query = str(query or '').strip()
    if not query:
        raise ValueError('Informe uma música ou link.')
    provider = _provider(query if query.startswith(('http://', 'https://')) else '')
    source_url = query if provider != 'unknown' else ''

    # Permissões ficam em cache durante a live; alterações do painel invalidam o cache.
    settings = _music_settings(bid)
    if provider == 'youtube' and not settings[0]:
        raise ValueError('YouTube está desativado para este canal.')
    if provider == 'spotify' and not settings[1]:
        raise ValueError('Spotify está desativado para este canal.')
    if provider == 'soundcloud' and not settings[2]:
        raise ValueError('SoundCloud está desativado para este canal.')
    if provider == 'link' and not settings[3]:
        raise ValueError('Links estão desativados para este canal.')
    if provider == 'unknown' and not settings[1]:
        raise ValueError('Spotify está desativado para este canal. Ative o Spotify para usar !addmusic por nome.')

    # Nome livre no chat: procura a faixa no Spotify conectado e grava
    # um URI spotify:track, que o player OBS já sabe reproduzir.
    if provider == 'unknown':
        found = _spotify_search_track(bid, query)
        provider = found['provider']
        source_url = found['source_url']
        title = found['title']
        artist = found['artist']
    else:
        if provider not in PLAYABLE_PROVIDERS:
            raise ValueError('Esta fonte ainda não é compatível com o player do OBS. Use YouTube, Spotify ou um link direto de áudio.')
        if provider == 'link' and not _is_direct_audio_url(source_url):
            raise ValueError('Para links, use uma URL direta de áudio (.mp3, .m4a, .aac, .ogg, .wav ou .opus).')
        title = query
        artist = ''
        if provider == 'spotify':
            # Aceita links https://open.spotify.com/track/... e URIs spotify:track:...
            from urllib.parse import urlparse
            if source_url.startswith('spotify:track:'):
                track_id = source_url.split(':', 2)[2].split('?', 1)[0].strip()
            else:
                parts = [part for part in urlparse(source_url).path.split('/') if part]
                track_id = parts[1].split('?', 1)[0] if len(parts) >= 2 and parts[0].lower() == 'track' else ''
            if not track_id or len(track_id) != 22:
                raise ValueError('Link do Spotify inválido. Use um link de faixa do Spotify.')
            # O player consegue reproduzir pelo URI; a busca opcional abaixo
            # apenas preenche título/artista quando possível.
            token = _spotify_access_token(bid)
            if token:
                try:
                    r = requests.get(
                        f'https://api.spotify.com/v1/tracks/{track_id}',
                        headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json'},
                        timeout=8,
                    )
                    if r.status_code < 400:
                        track = r.json() or {}
                        title = str(track.get('name') or title).strip()
                        artist = ', '.join(str(a.get('name') or '').strip() for a in (track.get('artists') or []) if a.get('name')).strip()
                except Exception:
                    pass
    if not source_url and ' - ' in query:
        artist, title = [part.strip() for part in query.split(' - ', 1)]
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # Serializa apenas inclusões do mesmo canal. Assim duas pessoas
            # pedindo a mesma música no mesmo instante não conseguem passar
            # simultaneamente pela verificação de duplicidade.
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"sn7-music-queue:{int(bid)}",))
            if _queue_duplicate_exists(cur, bid, provider, source_url, title, artist):
                raise ValueError(f'🎵 "{title}" já está na fila.')
            cur.execute("SELECT COUNT(*), COALESCE(MAX(position),0) FROM music_queue WHERE broadcaster_user_id=%s AND status='queued'", (int(bid),))
            queued_count, max_position = cur.fetchone()
            queued_count = int(queued_count or 0)
            if queued_count >= 100:
                raise ValueError('A fila deste canal já atingiu o limite de 100 músicas.')
            position = int(max_position or 0) + 1
            cur.execute("INSERT INTO music_queue(broadcaster_user_id,provider,title,artist,source_url,added_by,status,position) VALUES(%s,%s,%s,%s,%s,%s,'queued',%s) RETURNING id", (int(bid), provider, title[:200], artist[:160], source_url[:1000], str(user)[:80], position))
            item_id = cur.fetchone()[0]
            # Primeira música: vira a atual e inicia automaticamente.
            cur.execute(
                "INSERT INTO music_player_state(broadcaster_user_id,current_queue_id,is_playing,position_ms,duration_ms,seek_position_ms) "
                "VALUES(%s,%s,TRUE,0,0,0) "
                "ON CONFLICT (broadcaster_user_id) DO UPDATE SET "
                "current_queue_id=COALESCE(music_player_state.current_queue_id,EXCLUDED.current_queue_id), "
                "is_playing=CASE WHEN music_player_state.current_queue_id IS NULL THEN TRUE ELSE music_player_state.is_playing END, "
                "position_ms=CASE WHEN music_player_state.current_queue_id IS NULL THEN 0 ELSE music_player_state.position_ms END, "
                "duration_ms=CASE WHEN music_player_state.current_queue_id IS NULL THEN 0 ELSE music_player_state.duration_ms END, "
                "seek_position_ms=CASE WHEN music_player_state.current_queue_id IS NULL THEN 0 ELSE music_player_state.seek_position_ms END, "
                "updated_at=NOW()",
                (int(bid), item_id),
            )
        conn.commit()
        _notify_queue_changed(bid)
        return {'id': item_id, 'title': title[:200], 'artist': artist[:160], 'provider': provider}, position
    finally:
        conn.close()


def current_and_queue(bid):
    """Retorna estado leve da fila, com cache curto para o painel."""
    bid = int(bid)
    now = time.monotonic()
    with _QUEUE_CACHE_LOCK:
        cached = _QUEUE_CACHE.get(bid)
        if cached and now - cached[0] < _QUEUE_CACHE_TTL:
            current, queue = cached[1], cached[2]
            return current, [dict(item) for item in queue]

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT s.current_queue_id,q.id,q.title,q.artist,q.provider,q.source_url,q.added_by
                   FROM music_player_state s
                   LEFT JOIN music_queue q
                     ON q.id=s.current_queue_id AND q.status='queued'
                  WHERE s.broadcaster_user_id=%s""",
                (bid,),
            )
            row = cur.fetchone()
            current = (
                {
                    'id': row[1], 'title': row[2], 'artist': row[3] or '',
                    'provider': row[4], 'source_url': row[5] or '', 'added_by': row[6] or ''
                }
                if row and row[1] else None
            )
            current_id = row[0] if row else None
            cur.execute(
                """SELECT id,title,artist,provider,added_by,position
                     FROM music_queue
                    WHERE broadcaster_user_id=%s AND status='queued'
                      AND id<>COALESCE(%s,0)
                    ORDER BY position,id LIMIT 100""",
                (bid, current_id),
            )
            queue = [
                {
                    'id': r[0], 'title': r[1], 'artist': r[2] or '',
                    'provider': r[3], 'added_by': r[4] or '', 'position': r[5]
                }
                for r in cur.fetchall()
            ]
    finally:
        conn.close()

    with _QUEUE_CACHE_LOCK:
        _QUEUE_CACHE[bid] = (time.monotonic(), current, queue)
    return current, [dict(item) for item in queue]


def set_playing(bid, playing):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO music_player_state(broadcaster_user_id,is_playing) VALUES(%s,%s) ON CONFLICT(broadcaster_user_id) DO UPDATE SET is_playing=EXCLUDED.is_playing,updated_at=NOW()", (int(bid), bool(playing)))
        conn.commit()
    finally:
        conn.close()


def skip_current(bid):
    """Avança a fila e registra a faixa atual no histórico para o botão anterior."""
    bid = int(bid)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT current_queue_id FROM music_player_state WHERE broadcaster_user_id=%s FOR UPDATE',
                (bid,),
            )
            row = cur.fetchone()
            current_id = row[0] if row else None

            if current_id:
                cur.execute(
                    'INSERT INTO music_play_history(broadcaster_user_id,queue_id) VALUES(%s,%s)',
                    (bid, int(current_id)),
                )
                cur.execute(
                    "UPDATE music_queue SET status='played' WHERE id=%s AND broadcaster_user_id=%s",
                    (current_id, bid),
                )

            cur.execute(
                "SELECT id,title,artist,provider,source_url,added_by FROM music_queue "
                "WHERE broadcaster_user_id=%s AND status='queued' "
                "ORDER BY position,id LIMIT 1",
                (bid,),
            )
            nxt = cur.fetchone()
            next_id = nxt[0] if nxt else None
            cur.execute(
                'UPDATE music_player_state SET current_queue_id=%s,is_playing=%s,position_ms=0,duration_ms=0,seek_position_ms=0,updated_at=NOW() WHERE broadcaster_user_id=%s',
                (next_id, bool(next_id), bid),
            )
        conn.commit()
        _notify_queue_changed(bid)
        return ({
            'id': nxt[0], 'title': nxt[1], 'artist': nxt[2] or '',
            'provider': nxt[3], 'source_url': nxt[4] or '', 'added_by': nxt[5] or '',
        } if nxt else None)
    finally:
        conn.close()


def skip_current_fast(bid):
    """Avança a fila e devolve o estado necessário para o player em uma única conexão."""
    bid = int(bid)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT current_queue_id,is_playing,volume,position_ms,duration_ms,seek_position_ms,seek_revision "
                "FROM music_player_state WHERE broadcaster_user_id=%s FOR UPDATE",
                (bid,),
            )
            state_row = cur.fetchone()
            if not state_row:
                cur.execute(
                    "INSERT INTO music_player_state(broadcaster_user_id,is_playing) VALUES(%s,FALSE) "
                    "ON CONFLICT(broadcaster_user_id) DO NOTHING",
                    (bid,),
                )
                cur.execute(
                    "SELECT current_queue_id,is_playing,volume,position_ms,duration_ms,seek_position_ms,seek_revision "
                    "FROM music_player_state WHERE broadcaster_user_id=%s FOR UPDATE",
                    (bid,),
                )
                state_row = cur.fetchone()

            current_id = state_row[0] if state_row else None
            if current_id:
                cur.execute(
                    "INSERT INTO music_play_history(broadcaster_user_id,queue_id) VALUES(%s,%s)",
                    (bid, int(current_id)),
                )
                cur.execute(
                    "UPDATE music_queue SET status='played' WHERE id=%s AND broadcaster_user_id=%s",
                    (current_id, bid),
                )

            cur.execute(
                "SELECT id,title,artist,provider,source_url,added_by FROM music_queue "
                "WHERE broadcaster_user_id=%s AND status='queued' ORDER BY position,id LIMIT 1",
                (bid,),
            )
            nxt = cur.fetchone()
            next_id = nxt[0] if nxt else None
            cur.execute(
                "UPDATE music_player_state SET current_queue_id=%s,is_playing=%s,position_ms=0,duration_ms=0,seek_position_ms=0,updated_at=NOW() "
                "WHERE broadcaster_user_id=%s",
                (next_id, bool(next_id), bid),
            )

            cur.execute(
                "SELECT allow_youtube,allow_spotify,allow_soundcloud,allow_links,public_commands "
                "FROM music_settings WHERE broadcaster_user_id=%s",
                (bid,),
            )
            settings_row = cur.fetchone() or (True, True, False, True, False)
            settings = {
                "allow_youtube": bool(settings_row[0]),
                "allow_spotify": bool(settings_row[1]),
                "allow_soundcloud": bool(settings_row[2]),
                "allow_links": bool(settings_row[3]),
                "public_commands": bool(settings_row[4]),
            }

            cur.execute(
                "SELECT id,provider,title,artist,source_url,added_by,status FROM music_queue "
                "WHERE id=%s AND broadcaster_user_id=%s",
                (next_id, bid),
            ) if next_id else None
            current_row = cur.fetchone() if next_id else None
            current = ({
                "id": current_row[0], "provider": current_row[1], "title": current_row[2],
                "artist": current_row[3] or "", "source_url": current_row[4] or "",
                "added_by": current_row[5] or "", "status": current_row[6]
            } if current_row else None)

            cur.execute(
                "SELECT id,provider,title,artist,source_url,added_by,status,position FROM music_queue "
                "WHERE broadcaster_user_id=%s AND status='queued' AND id<>COALESCE(%s,0) "
                "ORDER BY position ASC,id ASC LIMIT 100",
                (bid, next_id),
            )
            queue = [{
                "id": r[0], "provider": r[1], "title": r[2], "artist": r[3] or "",
                "source_url": r[4] or "", "added_by": r[5] or "", "status": r[6], "position": r[7]
            } for r in cur.fetchall()]

            state = {
                "current_queue_id": next_id,
                "is_playing": bool(next_id),
                "volume": int(state_row[2] if state_row else 80),
                "position_ms": 0,
                "duration_ms": 0,
                "seek_position_ms": 0,
                "seek_revision": int(state_row[6] if state_row else 0),
            }
        conn.commit()
        _notify_queue_changed(bid)
        return {"ok": True, "settings": settings, "state": state, "current": current, "queue": queue}
    finally:
        conn.close()

def previous_current(bid):
    """Volta uma faixa no histórico de reprodução, preservando a fila."""
    bid = int(bid)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT current_queue_id,is_playing FROM music_player_state WHERE broadcaster_user_id=%s FOR UPDATE',
                (bid,),
            )
            state_row = cur.fetchone()
            was_playing = bool(state_row[1]) if state_row else False
            current_id = state_row[0] if state_row else None

            cur.execute(
                "SELECT h.id,h.queue_id,q.id,q.title,q.artist,q.provider,q.source_url,q.added_by "
                "FROM music_play_history h "
                "JOIN music_queue q ON q.id=h.queue_id AND q.broadcaster_user_id=h.broadcaster_user_id "
                "WHERE h.broadcaster_user_id=%s ORDER BY h.id DESC LIMIT 1 FOR UPDATE OF h,q",
                (bid,),
            )
            row = cur.fetchone()
            if not row:
                return None

            history_id, previous_id = int(row[0]), int(row[1])
            if current_id and int(current_id) != previous_id:
                cur.execute(
                    "UPDATE music_queue SET status='queued', position="
                    "(SELECT COALESCE(MIN(position),0)-1 FROM music_queue WHERE broadcaster_user_id=%s AND status='queued') "
                    "WHERE id=%s AND broadcaster_user_id=%s",
                    (bid, int(current_id), bid),
                )

            cur.execute(
                "UPDATE music_queue SET status='queued', position="
                "(SELECT COALESCE(MIN(position),0)-1 FROM music_queue WHERE broadcaster_user_id=%s AND status='queued' AND id<>%s) "
                "WHERE id=%s AND broadcaster_user_id=%s",
                (bid, previous_id, previous_id, bid),
            )
            cur.execute('DELETE FROM music_play_history WHERE id=%s AND broadcaster_user_id=%s', (history_id, bid))
            cur.execute(
                'UPDATE music_player_state SET current_queue_id=%s,is_playing=%s,position_ms=0,duration_ms=0,seek_position_ms=0,updated_at=NOW() WHERE broadcaster_user_id=%s',
                (previous_id, was_playing, bid),
            )

        conn.commit()
        _notify_queue_changed(bid)
        return {
            'id': row[2], 'title': row[3], 'artist': row[4] or '',
            'provider': row[5], 'source_url': row[6] or '', 'added_by': row[7] or '',
        }
    finally:
        conn.close()


def select_current(bid, queue_id):
    """Seleciona uma faixa da fila para tocar agora e devolve a atual para a fila."""
    bid = int(bid)
    queue_id = int(queue_id)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT current_queue_id,is_playing
                   FROM music_player_state
                   WHERE broadcaster_user_id=%s
                   FOR UPDATE""",
                (bid,),
            )
            state_row = cur.fetchone()
            if not state_row:
                cur.execute(
                    """INSERT INTO music_player_state(broadcaster_user_id,is_playing)
                       VALUES(%s,FALSE)
                       ON CONFLICT(broadcaster_user_id) DO NOTHING""",
                    (bid,),
                )
                cur.execute(
                    """SELECT current_queue_id,is_playing
                       FROM music_player_state
                       WHERE broadcaster_user_id=%s
                       FOR UPDATE""",
                    (bid,),
                )
                state_row = cur.fetchone()

            current_id = int(state_row[0]) if state_row and state_row[0] else None
            was_playing = bool(state_row[1]) if state_row else False

            cur.execute(
                """SELECT id,position,title,artist,provider,source_url,added_by
                   FROM music_queue
                   WHERE id=%s AND broadcaster_user_id=%s AND status='queued'
                   FOR UPDATE""",
                (queue_id, bid),
            )
            target = cur.fetchone()
            if not target:
                return None

            if current_id == queue_id:
                cur.execute(
                    """SELECT id,title,artist,provider,source_url,added_by
                       FROM music_queue
                       WHERE id=%s AND broadcaster_user_id=%s""",
                    (queue_id, bid),
                )
                target = cur.fetchone()
                return {
                    'id': target[0], 'title': target[1], 'artist': target[2] or '',
                    'provider': target[3], 'source_url': target[4] or '', 'added_by': target[5] or '',
                }

            target_position = int(target[1] or 0)

            if current_id:
                cur.execute(
                    """UPDATE music_queue
                       SET status='queued', position=%s
                       WHERE id=%s AND broadcaster_user_id=%s""",
                    (target_position, current_id, bid),
                )

            cur.execute(
                """UPDATE music_queue
                   SET status='queued', position=%s
                   WHERE id=%s AND broadcaster_user_id=%s""",
                (target_position - 1, queue_id, bid),
            )

            cur.execute(
                """UPDATE music_player_state
                   SET current_queue_id=%s,is_playing=%s,
                       position_ms=0,duration_ms=0,seek_position_ms=0,
                       updated_at=NOW()
                   WHERE broadcaster_user_id=%s""",
                (queue_id, was_playing, bid),
            )

        conn.commit()
        _notify_queue_changed(bid)
        return {
            'id': target[0], 'title': target[2], 'artist': target[3] or '',
            'provider': target[4], 'source_url': target[5] or '', 'added_by': target[6] or '',
        }
    finally:
        conn.close()

def clear_queue(bid):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM music_queue WHERE broadcaster_user_id=%s AND status='queued'", (int(bid),))
            cur.execute("DELETE FROM music_play_history WHERE broadcaster_user_id=%s", (int(bid),))
            cur.execute('UPDATE music_player_state SET current_queue_id=NULL,is_playing=FALSE,position_ms=0,duration_ms=0,seek_position_ms=0,updated_at=NOW() WHERE broadcaster_user_id=%s', (int(bid),))
        conn.commit()
        _notify_queue_changed(bid)
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
