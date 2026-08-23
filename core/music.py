from urllib.parse import urlparse
import time
import os
import base64
import re
import requests

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


PLAYABLE_PROVIDERS = {'youtube', 'spotify', 'link'}
DIRECT_AUDIO_EXTENSIONS = ('.mp3', '.m4a', '.aac', '.ogg', '.wav', '.opus')


def _is_direct_audio_url(url):
    path = urlparse(str(url or '').strip()).path.lower()
    return path.endswith(DIRECT_AUDIO_EXTENSIONS)


def _spotify_access_token(bid):
    """Retorna um token Spotify válido para buscas feitas pelo !addmusic."""
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
        return str(access_token)
    if not refresh_token:
        return str(access_token) if access_token else None

    client_id = os.environ.get('SPOTIFY_CLIENT_ID', '').strip()
    client_secret = os.environ.get('SPOTIFY_CLIENT_SECRET', '').strip()
    if not client_id or not client_secret:
        return str(access_token) if access_token else None

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
        return new_token
    except Exception:
        return str(access_token) if access_token else None


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
        # Primeiro pedimos ao Spotify uma busca filtrada pelo campo track.
        # Isso evita que a ordenação por popularidade faça "JUJUTSU 3"
        # aparecer antes de uma faixa chamada exatamente "JUJUTSU".
        safe_query = query.replace('"', ' ').strip()
        exact_items = search(f'track:"{safe_query}"')
        exact_track = choose_exact(exact_items)
        if exact_track:
            track = exact_track
        else:
            items = search(query)
            if not items:
                raise ValueError(f'Não encontrei "{query}" no Spotify.')
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

def add_from_chat(bid, query, user):
    query = str(query or '').strip()
    if not query:
        raise ValueError('Informe uma música ou link.')
    provider = _provider(query if query.startswith(('http://', 'https://')) else '')
    source_url = query if provider != 'unknown' else ''

    # Carrega as permissões antes de consultar qualquer serviço externo.
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT allow_youtube,allow_spotify,allow_soundcloud,allow_links FROM music_settings WHERE broadcaster_user_id=%s', (int(bid),))
            settings = cur.fetchone() or (True, True, False, True)
    finally:
        conn.close()

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
