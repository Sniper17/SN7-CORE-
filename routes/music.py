from flask import Blueprint, jsonify, request
from core.database import get_conn

music_bp = Blueprint('music', __name__)


def _settings(bid):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('''
                SELECT allow_youtube, allow_spotify, allow_soundcloud, allow_links
                FROM music_settings WHERE broadcaster_user_id=%s
            ''', (int(bid),))
            row = cur.fetchone()
            if not row:
                cur.execute('''
                    INSERT INTO music_settings (broadcaster_user_id)
                    VALUES (%s)
                    ON CONFLICT (broadcaster_user_id) DO NOTHING
                ''', (int(bid),))
                conn.commit()
                return {'allow_youtube': True, 'allow_spotify': True, 'allow_soundcloud': False, 'allow_links': True}
            return {
                'allow_youtube': bool(row[0]), 'allow_spotify': bool(row[1]),
                'allow_soundcloud': bool(row[2]), 'allow_links': bool(row[3])
            }
    finally:
        conn.close()


def _state(bid):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('''
                SELECT current_queue_id, is_playing, volume
                FROM music_player_state WHERE broadcaster_user_id=%s
            ''', (int(bid),))
            row = cur.fetchone()
            if not row:
                cur.execute('''
                    INSERT INTO music_player_state (broadcaster_user_id)
                    VALUES (%s)
                    ON CONFLICT (broadcaster_user_id) DO NOTHING
                ''', (int(bid),))
                conn.commit()
                return {'current_queue_id': None, 'is_playing': False, 'volume': 80}
            return {'current_queue_id': row[0], 'is_playing': bool(row[1]), 'volume': int(row[2])}
    finally:
        conn.close()


def _queue(bid):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('''
                SELECT id, provider, title, artist, source_url, added_by, status, position
                FROM music_queue
                WHERE broadcaster_user_id=%s AND status='queued'
                ORDER BY position ASC, id ASC
                LIMIT 100
            ''', (int(bid),))
            return [
                {'id': r[0], 'provider': r[1], 'title': r[2], 'artist': r[3],
                 'source_url': r[4] or '', 'added_by': r[5] or '', 'status': r[6], 'position': r[7]}
                for r in cur.fetchall()
            ]
    finally:
        conn.close()



def _ensure_current(bid):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_queue_id FROM music_player_state WHERE broadcaster_user_id=%s", (int(bid),))
            row = cur.fetchone()
            current_id = row[0] if row else None
            if current_id:
                cur.execute("SELECT 1 FROM music_queue WHERE id=%s AND broadcaster_user_id=%s AND status='queued'", (current_id, int(bid)))
                if cur.fetchone():
                    conn.commit()
                    return current_id
            cur.execute("SELECT id FROM music_queue WHERE broadcaster_user_id=%s AND status='queued' ORDER BY position ASC,id ASC LIMIT 1", (int(bid),))
            nxt = cur.fetchone()
            current_id = nxt[0] if nxt else None
            cur.execute("UPDATE music_player_state SET current_queue_id=%s, updated_at=NOW() WHERE broadcaster_user_id=%s", (current_id, int(bid)))
        conn.commit()
        return current_id
    finally:
        conn.close()


def snapshot(bid):
    _ensure_current(bid)
    state = _state(bid)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            current = None
            if state['current_queue_id']:
                cur.execute('''
                    SELECT id, provider, title, artist, source_url, added_by, status
                    FROM music_queue WHERE id=%s AND broadcaster_user_id=%s
                ''', (state['current_queue_id'], int(bid)))
                r = cur.fetchone()
                if r:
                    current = {'id': r[0], 'provider': r[1], 'title': r[2], 'artist': r[3],
                               'source_url': r[4] or '', 'added_by': r[5] or '', 'status': r[6]}
    finally:
        conn.close()
    return {'ok': True, 'settings': _settings(bid), 'state': state, 'current': current, 'queue': _queue(bid)}


@music_bp.get('/<int:broadcaster_id>')
def get_music(broadcaster_id):
    try:
        return jsonify(snapshot(broadcaster_id))
    except Exception as exc:
        print(f'[MUSIC] GET erro: {exc}', flush=True)
        return jsonify({'ok': False, 'error': str(exc)}), 500


@music_bp.patch('/<int:broadcaster_id>/settings')
def update_music_settings(broadcaster_id):
    data = request.get_json(silent=True) or {}
    allowed = {'allow_youtube', 'allow_spotify', 'allow_soundcloud', 'allow_links'}
    values = {k: bool(data[k]) for k in allowed if k in data}
    if not values:
        return jsonify({'ok': False, 'error': 'Nenhuma configuração válida foi enviada.'}), 400
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('''
                INSERT INTO music_settings (broadcaster_user_id)
                VALUES (%s) ON CONFLICT (broadcaster_user_id) DO NOTHING
            ''', (int(broadcaster_id),))
            sets = ', '.join(f'{k}=%s' for k in values)
            cur.execute(f'UPDATE music_settings SET {sets}, updated_at=NOW() WHERE broadcaster_user_id=%s',
                        [*values.values(), int(broadcaster_id)])
        conn.commit()
    finally:
        conn.close()
    return jsonify(snapshot(broadcaster_id))


@music_bp.patch('/<int:broadcaster_id>/state')
def update_music_state(broadcaster_id):
    data = request.get_json(silent=True) or {}
    sets, vals = [], []
    if 'is_playing' in data:
        sets.append('is_playing=%s'); vals.append(bool(data['is_playing']))
    if 'volume' in data:
        volume = max(0, min(100, int(data['volume'])))
        sets.append('volume=%s'); vals.append(volume)
    if not sets:
        return jsonify({'ok': False, 'error': 'Estado inválido.'}), 400
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('''INSERT INTO music_player_state (broadcaster_user_id) VALUES (%s)
                           ON CONFLICT (broadcaster_user_id) DO NOTHING''', (int(broadcaster_id),))
            cur.execute(f'UPDATE music_player_state SET {", ".join(sets)}, updated_at=NOW() WHERE broadcaster_user_id=%s',
                        [*vals, int(broadcaster_id)])
        conn.commit()
    finally:
        conn.close()
    return jsonify(snapshot(broadcaster_id))


@music_bp.post('/<int:broadcaster_id>/queue')
def add_music(broadcaster_id):
    data = request.get_json(silent=True) or {}
    title = str(data.get('title') or '').strip()
    artist = str(data.get('artist') or '').strip()
    source_url = str(data.get('source_url') or '').strip()
    provider = str(data.get('provider') or 'unknown').strip().lower()
    added_by = str(data.get('added_by') or '').strip()[:80]
    if not title:
        return jsonify({'ok': False, 'error': 'Informe o nome da música.'}), 400
    if len(title) > 200 or len(artist) > 160:
        return jsonify({'ok': False, 'error': 'Nome da música ou artista muito longo.'}), 400
    if provider not in {'youtube', 'spotify', 'soundcloud', 'link', 'unknown'}:
        return jsonify({'ok': False, 'error': 'Fonte de música inválida.'}), 400
    if provider == 'youtube' and not _settings(broadcaster_id)['allow_youtube']:
        return jsonify({'ok': False, 'error': 'YouTube está desativado nas fontes do canal.'}), 403
    if provider == 'spotify' and not _settings(broadcaster_id)['allow_spotify']:
        return jsonify({'ok': False, 'error': 'Spotify está desativado nas fontes do canal.'}), 403
    if provider == 'soundcloud' and not _settings(broadcaster_id)['allow_soundcloud']:
        return jsonify({'ok': False, 'error': 'SoundCloud está desativado nas fontes do canal.'}), 403
    if source_url and not _settings(broadcaster_id)['allow_links']:
        return jsonify({'ok': False, 'error': 'Links estão desativados nas fontes do canal.'}), 403

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT COALESCE(MAX(position),0)+1 FROM music_queue WHERE broadcaster_user_id=%s AND status=\'queued\'', (int(broadcaster_id),))
            position = int(cur.fetchone()[0] or 1)
            cur.execute('''
                INSERT INTO music_queue (broadcaster_user_id, provider, title, artist, source_url, added_by, status, position)
                VALUES (%s,%s,%s,%s,%s,%s,'queued',%s) RETURNING id
            ''', (int(broadcaster_id), provider, title, artist, source_url, added_by, position))
            item_id = cur.fetchone()[0]
        conn.commit()
    finally:
        conn.close()
    return jsonify({**snapshot(broadcaster_id), 'added_id': item_id})


@music_bp.post('/<int:broadcaster_id>/queue/<int:item_id>/remove')
def remove_music(broadcaster_id, item_id):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('''DELETE FROM music_queue WHERE id=%s AND broadcaster_user_id=%s AND status='queued' ''',
                        (item_id, int(broadcaster_id)))
        conn.commit()
    finally:
        conn.close()
    return jsonify(snapshot(broadcaster_id))


@music_bp.post('/<int:broadcaster_id>/skip')
def skip_music(broadcaster_id):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_queue_id FROM music_player_state WHERE broadcaster_user_id=%s FOR UPDATE", (int(broadcaster_id),))
            row = cur.fetchone()
            current_id = row[0] if row else None
            if current_id:
                cur.execute("UPDATE music_queue SET status='played' WHERE id=%s AND broadcaster_user_id=%s", (current_id, int(broadcaster_id)))
            cur.execute("SELECT id FROM music_queue WHERE broadcaster_user_id=%s AND status='queued' ORDER BY position ASC,id ASC LIMIT 1", (int(broadcaster_id),))
            nxt = cur.fetchone()
            next_id = nxt[0] if nxt else None
            cur.execute("UPDATE music_player_state SET current_queue_id=%s, is_playing=%s, updated_at=NOW() WHERE broadcaster_user_id=%s", (next_id, bool(next_id), int(broadcaster_id)))
        conn.commit()
    finally:
        conn.close()
    return jsonify(snapshot(broadcaster_id))


@music_bp.post('/<int:broadcaster_id>/queue/clear')
def clear_music(broadcaster_id):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM music_queue WHERE broadcaster_user_id=%s AND status='queued'", (int(broadcaster_id),))
        conn.commit()
    finally:
        conn.close()
    return jsonify(snapshot(broadcaster_id))
