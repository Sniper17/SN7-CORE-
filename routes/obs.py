import base64
import os
import secrets
import time
from threading import Lock
from urllib.parse import urlparse, parse_qs

import requests
from flask import Blueprint, jsonify, render_template, request

from core.database import get_conn
from core.auth import require_session_broadcaster

obs_bp = Blueprint('obs', __name__)
_overlay_rate_lock = Lock()
_overlay_rate = {}
_OVERLAY_RATE_WINDOW = 10.0
_OVERLAY_RATE_LIMIT = 80

# O token do overlay é validado repetidamente enquanto o OBS está aberto.
# Cache curtíssimo evita uma consulta ao PostgreSQL a cada polling sem deixar
# uma conexão revogada válida por muito tempo.
_overlay_token_lock = Lock()
_overlay_token_cache = {}
_OVERLAY_TOKEN_TTL = 10.0


def _public_base_url():
    configured = request.headers.get('X-Forwarded-Proto')
    proto = configured or request.scheme
    return f"{proto}://{request.host}".rstrip('/')


def _get_connection(broadcaster_id):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT broadcaster_user_id, access_token, label, created_at, updated_at "
                "FROM obs_connections WHERE broadcaster_user_id=%s",
                (int(broadcaster_id),),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return {
        'broadcaster_user_id': int(row[0]),
        'access_token': row[1],
        'label': row[2] or 'SN7 Core',
        'created_at': row[11].isoformat() if row[3] else None,
        'updated_at': row[12].isoformat() if row[4] else None,
    }


def _save_connection(broadcaster_id, token):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO obs_connections (broadcaster_user_id, access_token, label, updated_at)
                   VALUES (%s,%s,'SN7 Core',NOW())
                   ON CONFLICT (broadcaster_user_id) DO UPDATE SET
                     access_token=EXCLUDED.access_token,
                     updated_at=NOW()""",
                (int(broadcaster_id), token),
            )
        conn.commit()
    finally:
        conn.close()
    with _overlay_token_lock:
        # O token antigo pode ter sido substituído durante uma reconexão.
        _overlay_token_cache.pop(str(token), None)


def _allow_overlay_request(token):
    now = time.monotonic()
    key = str(token or '').strip()
    with _overlay_rate_lock:
        timestamps = _overlay_rate.get(key, [])
        cutoff = now - _OVERLAY_RATE_WINDOW
        timestamps = [value for value in timestamps if value > cutoff]
        if len(timestamps) >= _OVERLAY_RATE_LIMIT:
            _overlay_rate[key] = timestamps
            return False
        timestamps.append(now)
        _overlay_rate[key] = timestamps
        if len(_overlay_rate) > 2048:
            oldest = min(_overlay_rate, key=lambda item: _overlay_rate[item][-1] if _overlay_rate[item] else 0)
            _overlay_rate.pop(oldest, None)
    return True


def _find_by_token(token):
    token = str(token or '').strip()
    if not token or len(token) > 200:
        return None

    now = time.monotonic()
    with _overlay_token_lock:
        cached = _overlay_token_cache.get(token)
        if cached and cached[0] > now:
            return dict(cached[1]) if cached[1] else None
        if cached:
            _overlay_token_cache.pop(token, None)

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT broadcaster_user_id, access_token, label FROM obs_connections WHERE access_token=%s",
                (token,),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    value = None
    if row:
        value = {
            'broadcaster_user_id': int(row[0]),
            'access_token': row[1],
            'label': row[2] or 'SN7 Core',
        }

    with _overlay_token_lock:
        _overlay_token_cache[token] = (
            now + _OVERLAY_TOKEN_TTL,
            dict(value) if value else None,
        )
        if len(_overlay_token_cache) > 2048:
            expired = [key for key, item in _overlay_token_cache.items() if item[0] <= now]
            for key in expired[:256]:
                _overlay_token_cache.pop(key, None)
    return value


def _snapshot_for_obs(broadcaster_id):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT s.current_queue_id, s.is_playing, s.volume,
                          s.position_ms, s.duration_ms, s.seek_position_ms, s.seek_revision,
                          q.id, q.provider, q.title, q.artist, q.source_url, q.added_by, q.status
                   FROM music_player_state s
                   LEFT JOIN music_queue q
                     ON q.id=s.current_queue_id
                    AND q.broadcaster_user_id=s.broadcaster_user_id
                   WHERE s.broadcaster_user_id=%s""",
                (int(broadcaster_id),),
            )
            row = cur.fetchone()
            state = {
                'current_queue_id': row[0] if row else None,
                'is_playing': bool(row[1]) if row else False,
                'volume': int(row[2]) if row else 80,
                'position_ms': max(0, int(row[3] or 0)) if row else 0,
                'duration_ms': max(0, int(row[4] or 0)) if row else 0,
                'seek_position_ms': max(0, int(row[5] or 0)) if row else 0,
                'seek_revision': int(row[6] or 0) if row else 0,
            }
            current = None
            if row and row[7]:
                current = {
                    'id': row[7],
                    'provider': row[8] or 'unknown',
                    'title': row[9] or '',
                    'artist': row[10] or '',
                    'source_url': row[11] or '',
                    'added_by': row[12] or '',
                    'status': row[13] or 'queued',
                }
            cur.execute(
                """SELECT id, provider, title, artist, source_url, added_by, status, position
                   FROM music_queue
                   WHERE broadcaster_user_id=%s AND status='queued' AND id<>COALESCE(%s,0)
                   ORDER BY position ASC,id ASC LIMIT 100""",
                (int(broadcaster_id), state['current_queue_id']),
            )
            queue = [
                {
                    'id': r[0], 'provider': r[1], 'title': r[2], 'artist': r[3] or '',
                    'source_url': r[4] or '', 'added_by': r[5] or '',
                    'status': r[6], 'position': r[7],
                }
                for r in cur.fetchall()
            ]
            return state, current, queue
    finally:
        conn.close()

def _set_playing(broadcaster_id, playing):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO music_player_state (broadcaster_user_id,is_playing)
                   VALUES (%s,%s)
                   ON CONFLICT (broadcaster_user_id) DO UPDATE SET
                     is_playing=EXCLUDED.is_playing, updated_at=NOW()""",
                (int(broadcaster_id), bool(playing)),
            )
        conn.commit()
    finally:
        conn.close()


def _skip(broadcaster_id):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT current_queue_id FROM music_player_state WHERE broadcaster_user_id=%s FOR UPDATE",
                (int(broadcaster_id),),
            )
            row = cur.fetchone()
            if not row:
                cur.execute(
                    "INSERT INTO music_player_state (broadcaster_user_id) VALUES (%s) ON CONFLICT (broadcaster_user_id) DO NOTHING",
                    (int(broadcaster_id),),
                )
                cur.execute(
                    "SELECT current_queue_id FROM music_player_state WHERE broadcaster_user_id=%s FOR UPDATE",
                    (int(broadcaster_id),),
                )
                row = cur.fetchone()
            current_id = row[0] if row else None
            if current_id:
                cur.execute(
                    "UPDATE music_queue SET status='played' WHERE id=%s AND broadcaster_user_id=%s",
                    (current_id, int(broadcaster_id)),
                )
            cur.execute(
                """SELECT id FROM music_queue
                   WHERE broadcaster_user_id=%s AND status='queued'
                   ORDER BY position ASC,id ASC LIMIT 1""",
                (int(broadcaster_id),),
            )
            nxt = cur.fetchone()
            next_id = nxt[0] if nxt else None
            cur.execute(
                """UPDATE music_player_state
                   SET current_queue_id=%s, is_playing=%s, updated_at=NOW()
                   WHERE broadcaster_user_id=%s""",
                (next_id, bool(next_id), int(broadcaster_id)),
            )
        conn.commit()
    finally:
        conn.close()


def _spotify_refresh_if_needed(broadcaster_id):
    """Return a usable Spotify access token, refreshing it when expired."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT access_token, refresh_token, expires_at
                   FROM music_connections
                   WHERE broadcaster_user_id=%s AND provider='spotify'""",
                (int(broadcaster_id),),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        return None

    access_token, refresh_token, expires_at = row
    now = int(time.time())
    if access_token and (not expires_at or int(expires_at) > now + 60):
        return access_token
    if not refresh_token:
        return access_token

    client_id = os.environ.get('SPOTIFY_CLIENT_ID', '').strip()
    client_secret = os.environ.get('SPOTIFY_CLIENT_SECRET', '').strip()
    if not client_id or not client_secret:
        return access_token

    basic = base64.b64encode(f'{client_id}:{client_secret}'.encode()).decode()
    try:
        response = requests.post(
            'https://accounts.spotify.com/api/token',
            data={'grant_type': 'refresh_token', 'refresh_token': refresh_token},
            headers={'Authorization': f'Basic {basic}', 'Accept': 'application/json'},
            timeout=12,
        )
        data = response.json()
        if response.status_code >= 400:
            return access_token
        new_token = data.get('access_token')
        if not new_token:
            return access_token
        expires_in = int(data.get('expires_in') or 3600)
        new_refresh = data.get('refresh_token') or refresh_token
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE music_connections
                       SET access_token=%s, refresh_token=%s, expires_at=%s, updated_at=NOW()
                       WHERE broadcaster_user_id=%s AND provider='spotify'""",
                    (new_token, new_refresh, now + expires_in, int(broadcaster_id)),
                )
            conn.commit()
        finally:
            conn.close()
        return new_token
    except Exception:
        return access_token


def _youtube_id(url):
    try:
        parsed = urlparse(str(url or '').strip())
        host = (parsed.netloc or '').lower()
        if 'youtu.be' in host:
            return parsed.path.strip('/').split('/')[0] or None
        if 'youtube.com' in host:
            if parsed.path == '/watch':
                return parse_qs(parsed.query).get('v', [None])[0]
            if parsed.path.startswith('/shorts/'):
                return parsed.path.split('/')[2]
            if parsed.path.startswith('/embed/'):
                return parsed.path.split('/')[2]
    except Exception:
        pass
    return None


@obs_bp.get('/api/obs/<int:broadcaster_id>/connection')
def get_obs_connection(broadcaster_id):
    try:
        require_session_broadcaster(broadcaster_id)
    except PermissionError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 403
    conn = _get_connection(broadcaster_id)
    if not conn:
        return jsonify({'ok': True, 'connected': False, 'connection': None})
    return jsonify({
        'ok': True,
        'connected': True,
        'connection': {
            'label': conn['label'],
            'created_at': conn['created_at'],
            'updated_at': conn['updated_at'],
            'overlay_url': f"{_public_base_url()}/overlay/{conn['access_token']}",
        },
    })


@obs_bp.post('/api/obs/<int:broadcaster_id>/connect')
def connect_obs(broadcaster_id):
    try:
        require_session_broadcaster(broadcaster_id)
    except PermissionError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 403
    existing = _get_connection(broadcaster_id)
    token = existing['access_token'] if existing else secrets.token_urlsafe(32)
    _save_connection(broadcaster_id, token)
    return jsonify({
        'ok': True,
        'connected': True,
        'connection': {
            'label': 'SN7 Core',
            'overlay_url': f"{_public_base_url()}/overlay/{token}",
        },
    })


@obs_bp.post('/api/obs/<int:broadcaster_id>/rotate')
def rotate_obs(broadcaster_id):
    try:
        require_session_broadcaster(broadcaster_id)
    except PermissionError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 403
    token = secrets.token_urlsafe(32)
    _save_connection(broadcaster_id, token)
    return jsonify({
        'ok': True,
        'connected': True,
        'connection': {
            'label': 'SN7 Core',
            'overlay_url': f"{_public_base_url()}/overlay/{token}",
        },
    })


@obs_bp.get('/status/<token>')
def overlay_status(token):
    if not _allow_overlay_request(token):
        return jsonify({'ok': False, 'error': 'Muitas solicitações. Aguarde alguns segundos.'}), 429
    conn = _find_by_token(token)
    if not conn:
        return jsonify({'ok': False, 'error': 'Conexão OBS inválida.'}), 404
    state, current, queue = _snapshot_for_obs(conn['broadcaster_user_id'])
    response = jsonify({
        'ok': True,
        'connected': True,
        'broadcaster_id': conn['broadcaster_user_id'],
        'state': state,
        'current': current,
        'queue': queue,
        'server_time': int(time.time()),
    })
    response.headers['Cache-Control'] = 'no-store, max-age=0'
    return response


@obs_bp.post('/control/<token>/skip')
def overlay_skip(token):
    if not _allow_overlay_request(token):
        return jsonify({'ok': False, 'error': 'Muitas solicitações. Aguarde alguns segundos.'}), 429
    conn = _find_by_token(token)
    if not conn:
        return jsonify({'ok': False, 'error': 'Conexão OBS inválida.'}), 404
    _skip(conn['broadcaster_user_id'])
    state, current, queue = _snapshot_for_obs(conn['broadcaster_user_id'])
    response = jsonify({'ok': True, 'state': state, 'current': current, 'queue': queue})
    response.headers['Cache-Control'] = 'no-store, max-age=0'
    return response


@obs_bp.get('/spotify-token/<token>')
def overlay_spotify_token(token):
    if not _allow_overlay_request(token):
        return jsonify({'ok': False, 'error': 'Muitas solicitações. Aguarde alguns segundos.'}), 429
    conn = _find_by_token(token)
    if not conn:
        return jsonify({'ok': False, 'error': 'Conexão OBS inválida.'}), 404
    access_token = _spotify_refresh_if_needed(conn['broadcaster_user_id'])
    if not access_token:
        return jsonify({'ok': False, 'error': 'Spotify não está conectado neste canal.'}), 404
    response = jsonify({'ok': True, 'access_token': access_token})
    response.headers['Cache-Control'] = 'no-store, max-age=0'
    return response



def _set_progress(broadcaster_id, position_ms, duration_ms):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO music_player_state
                   (broadcaster_user_id,position_ms,duration_ms)
                   VALUES (%s,%s,%s)
                   ON CONFLICT (broadcaster_user_id) DO UPDATE SET
                     position_ms=EXCLUDED.position_ms,
                     duration_ms=EXCLUDED.duration_ms,
                     updated_at=NOW()""",
                (int(broadcaster_id), max(0, int(position_ms or 0)), max(0, int(duration_ms or 0))),
            )
        conn.commit()
    finally:
        conn.close()




@obs_bp.post('/control/<token>/heartbeat')
def overlay_heartbeat(token):
    if not _allow_overlay_request(token):
        return jsonify({'ok': False, 'error': 'Muitas solicitações. Aguarde alguns segundos.'}), 429
    conn = _find_by_token(token)
    if not conn:
        return jsonify({'ok': False, 'error': 'Conexão OBS inválida.'}), 404
    db = get_conn()
    try:
        with db.cursor() as cur:
            cur.execute(
                "UPDATE obs_connections SET updated_at=NOW() WHERE broadcaster_user_id=%s",
                (int(conn['broadcaster_user_id']),),
            )
        db.commit()
    finally:
        db.close()
    return jsonify({'ok': True})

@obs_bp.post('/control/<token>/progress')
def overlay_progress(token):
    if not _allow_overlay_request(token):
        return jsonify({'ok': False, 'error': 'Muitas solicitações. Aguarde alguns segundos.'}), 429
    conn = _find_by_token(token)
    if not conn:
        return jsonify({'ok': False, 'error': 'Conexão OBS inválida.'}), 404
    data = request.get_json(silent=True) or {}
    _set_progress(
        conn['broadcaster_user_id'],
        data.get('position_ms', 0),
        data.get('duration_ms', 0),
    )
    return jsonify({'ok': True})


@obs_bp.post('/control/<token>/seek')
def overlay_seek(token):
    if not _allow_overlay_request(token):
        return jsonify({'ok': False, 'error': 'Muitas solicitações. Aguarde alguns segundos.'}), 429
    conn = _find_by_token(token)
    if not conn:
        return jsonify({'ok': False, 'error': 'Conexão OBS inválida.'}), 404
    data = request.get_json(silent=True) or {}
    position_ms = max(0, int(data.get('position_ms', 0) or 0))
    db = get_conn()
    try:
        with db.cursor() as cur:
            cur.execute(
                """INSERT INTO music_player_state
                   (broadcaster_user_id,seek_position_ms,seek_revision)
                   VALUES (%s,%s,1)
                   ON CONFLICT (broadcaster_user_id) DO UPDATE SET
                     seek_position_ms=EXCLUDED.seek_position_ms,
                     seek_revision=music_player_state.seek_revision+1,
                     updated_at=NOW()""",
                (int(conn['broadcaster_user_id']), position_ms),
            )
        db.commit()
    finally:
        db.close()
    return jsonify({'ok': True, 'position_ms': position_ms})

@obs_bp.post('/control/<token>/playing')
def overlay_playing(token):
    if not _allow_overlay_request(token):
        return jsonify({'ok': False, 'error': 'Muitas solicitações. Aguarde alguns segundos.'}), 429
    conn = _find_by_token(token)
    if not conn:
        return jsonify({'ok': False, 'error': 'Conexão OBS inválida.'}), 404
    data = request.get_json(silent=True) or {}
    _set_playing(conn['broadcaster_user_id'], bool(data.get('is_playing')))
    state, current, queue = _snapshot_for_obs(conn['broadcaster_user_id'])
    response = jsonify({'ok': True, 'state': state, 'current': current, 'queue': queue})
    response.headers['Cache-Control'] = 'no-store, max-age=0'
    return response



@obs_bp.get('/overlay/<token>')
def obs_overlay(token):
    conn = _find_by_token(token)
    if not conn:
        response = render_template('overlay.html', invalid=True, token='')
        response.status_code = 404
        response.headers['Cache-Control'] = 'no-store, max-age=0'
        return response
    response = render_template('overlay.html', invalid=False, token=token)
    response.headers['Cache-Control'] = 'no-store, max-age=0'
    return response
