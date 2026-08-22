import secrets
from flask import Blueprint, jsonify, render_template, request
from core.database import get_conn
from core.auth import require_session_broadcaster

obs_bp = Blueprint('obs', __name__)


def _public_base_url():
    configured = request.headers.get('X-Forwarded-Proto')
    proto = configured or request.scheme
    return f"{proto}://{request.host}".rstrip('/')


def _get_connection(broadcaster_id):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT broadcaster_user_id, access_token, label, created_at, updated_at FROM obs_connections WHERE broadcaster_user_id=%s",
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
        'created_at': row[3].isoformat() if row[3] else None,
        'updated_at': row[4].isoformat() if row[4] else None,
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
    conn = _find_by_token(token)
    if not conn:
        return jsonify({'ok': False, 'error': 'Conexão OBS inválida.'}), 404
    from core.music import current_and_queue
    try:
        current, queue = current_and_queue(conn['broadcaster_user_id'])
    except Exception:
        current, queue = None, []
    return jsonify({'ok': True, 'connected': True, 'broadcaster_id': conn['broadcaster_user_id'], 'current': current, 'queue': queue})


def _find_by_token(token):
    token = str(token or '').strip()
    if not token or len(token) > 200:
        return None
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT broadcaster_user_id, access_token, label FROM obs_connections WHERE access_token=%s", (token,))
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return {'broadcaster_user_id': int(row[0]), 'access_token': row[1], 'label': row[2] or 'SN7 Core'}


@obs_bp.get('/overlay/<token>')
def obs_overlay(token):
    conn = _find_by_token(token)
    if not conn:
        return render_template('overlay.html', invalid=True), 404
    return render_template('overlay.html', invalid=False, token=token)
