from flask import Blueprint, jsonify, request, redirect, session
from core.database import get_conn
from core.auth import require_session_broadcaster
from core.music import (
    set_public_commands_cache, _spotify_access_token, clear_queue, previous_current, select_current, _queue_duplicate_exists,
    current_and_queue, skip_current_fast, invalidate_queue_cache, invalidate_music_settings_cache,
)
import os
import time
import secrets
import base64
import hashlib
from urllib.parse import urlencode
import requests

music_bp = Blueprint('music', __name__)


def _settings(bid):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('''
                SELECT allow_youtube, allow_spotify, allow_soundcloud, allow_links, public_commands
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
                return {'allow_youtube': True, 'allow_spotify': True, 'allow_soundcloud': False, 'allow_links': False, 'public_commands': False}
            return {
                'allow_youtube': bool(row[0]), 'allow_spotify': bool(row[1]),
                'allow_soundcloud': bool(row[2]), 'allow_links': bool(row[3]),
                'public_commands': bool(row[4])
            }
    finally:
        conn.close()


def snapshot(bid):
    """Build the player snapshot with a single DB connection."""
    bid = int(bid)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT allow_youtube, allow_spotify, allow_soundcloud, allow_links, public_commands
                   FROM music_settings WHERE broadcaster_user_id=%s""",
                (bid,),
            )
            settings_row = cur.fetchone()
            if not settings_row:
                cur.execute(
                    "INSERT INTO music_settings (broadcaster_user_id) VALUES (%s) ON CONFLICT (broadcaster_user_id) DO NOTHING",
                    (bid,),
                )
                settings_row = (True, True, False, True, False)

            settings = {
                'allow_youtube': bool(settings_row[0]),
                'allow_spotify': bool(settings_row[1]),
                'allow_soundcloud': bool(settings_row[2]),
                'allow_links': bool(settings_row[3]),
                'public_commands': bool(settings_row[4]),
            }

            cur.execute(
                "SELECT current_queue_id, is_playing, volume, position_ms, duration_ms, seek_position_ms, seek_revision "
                "FROM music_player_state WHERE broadcaster_user_id=%s",
                (bid,),
            )
            state_row = cur.fetchone()
            if not state_row:
                cur.execute(
                    "INSERT INTO music_player_state (broadcaster_user_id) VALUES (%s) ON CONFLICT (broadcaster_user_id) DO NOTHING",
                    (bid,),
                )
                state_row = (None, False, 80, 0, 0, 0, 0)

            current_id = state_row[0]
            if current_id:
                cur.execute(
                    "SELECT 1 FROM music_queue WHERE id=%s AND broadcaster_user_id=%s AND status='queued'",
                    (current_id, bid),
                )
                if not cur.fetchone():
                    current_id = None

            if current_id is None:
                cur.execute(
                    "SELECT id FROM music_queue WHERE broadcaster_user_id=%s AND status='queued' ORDER BY position ASC,id ASC LIMIT 1",
                    (bid,),
                )
                next_row = cur.fetchone()
                current_id = next_row[0] if next_row else None
                cur.execute(
                    "UPDATE music_player_state SET current_queue_id=%s, updated_at=NOW() WHERE broadcaster_user_id=%s",
                    (current_id, bid),
                )

            state = {
                'current_queue_id': current_id,
                'is_playing': bool(state_row[1]),
                'volume': int(state_row[2]),
                'position_ms': max(0, int(state_row[3] or 0)),
                'duration_ms': max(0, int(state_row[4] or 0)),
                'seek_position_ms': max(0, int(state_row[5] or 0)),
                'seek_revision': int(state_row[6] or 0),
            }

            current = None
            if current_id:
                cur.execute(
                    """SELECT id, provider, title, artist, source_url, added_by, status
                       FROM music_queue WHERE id=%s AND broadcaster_user_id=%s""",
                    (current_id, bid),
                )
                row = cur.fetchone()
                if row:
                    current = {
                        'id': row[0], 'provider': row[1], 'title': row[2], 'artist': row[3] or '',
                        'source_url': row[4] or '', 'added_by': row[5] or '', 'status': row[6],
                    }

            cur.execute(
                """SELECT id, provider, title, artist, source_url, added_by, status, position
                   FROM music_queue
                   WHERE broadcaster_user_id=%s AND status='queued'
                     AND (id<>COALESCE(%s,0) OR NOT %s)
                   ORDER BY position ASC,id ASC LIMIT 100""",
                (bid, current_id, bool(state_row[1])),
            )
            queue = [
                {'id': r[0], 'provider': r[1], 'title': r[2], 'artist': r[3] or '',
                 'source_url': r[4] or '', 'added_by': r[5] or '', 'status': r[6], 'position': r[7]}
                for r in cur.fetchall()
            ]
        conn.commit()
        return {'ok': True, 'settings': settings, 'state': state, 'current': current, 'queue': queue}
    finally:
        conn.close()


@music_bp.get('/<int:broadcaster_id>')
def get_music(broadcaster_id):
    try:
        return jsonify(snapshot(broadcaster_id))
    except Exception as exc:
        print(f'[MUSIC] GET erro: {exc}', flush=True)
        return jsonify({'ok': False, 'error': str(exc)}), 500




@music_bp.get('/<int:broadcaster_id>/obs-status')
def obs_status(broadcaster_id):
    try:
        require_session_broadcaster(broadcaster_id)
    except PermissionError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 401
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT updated_at FROM obs_connections WHERE broadcaster_user_id=%s",
                (int(broadcaster_id),),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    now = time.time()
    last_seen = None
    connected = False
    if row and row[0]:
        last_seen = row[0].timestamp()
        connected = (now - last_seen) <= 6
    return jsonify({
        'ok': True,
        'connected': connected,
        'last_seen': int(last_seen) if last_seen else None,
    })

@music_bp.get('/<int:broadcaster_id>/queue')
def get_music_queue(broadcaster_id):
    """Endpoint leve usado pelo painel para sincronizar a fila rapidamente."""
    try:
        current, queue = current_and_queue(broadcaster_id)
        return jsonify({'ok': True, 'current': current, 'queue': queue})
    except Exception as exc:
        print(f'[MUSIC] GET fila erro: {exc}', flush=True)
        return jsonify({'ok': False, 'error': str(exc)}), 500


@music_bp.patch('/<int:broadcaster_id>/settings')
def update_music_settings(broadcaster_id):
    try:
        require_session_broadcaster(broadcaster_id)
    except PermissionError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 401
    data = request.get_json(silent=True) or {}
    allowed = {'allow_youtube', 'allow_spotify', 'allow_soundcloud', 'allow_links', 'public_commands'}
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
        if 'public_commands' in values:
            set_public_commands_cache(broadcaster_id, values['public_commands'])
        invalidate_music_settings_cache(broadcaster_id)
    finally:
        conn.close()

    # Do not rebuild the full player snapshot just to save these settings.
    # Returning the saved values keeps the dashboard response lightweight.
    saved_settings = {
        'allow_youtube': bool(values.get('allow_youtube', True)),
        'allow_spotify': bool(values.get('allow_spotify', True)),
        'allow_soundcloud': bool(values.get('allow_soundcloud', False)),
        'allow_links': bool(values.get('allow_links', True)),
        'public_commands': bool(values.get('public_commands', False)),
    }
    return jsonify({'ok': True, 'settings': saved_settings})


@music_bp.patch('/<int:broadcaster_id>/state')
def update_music_state(broadcaster_id):
    try:
        require_session_broadcaster(broadcaster_id)
    except PermissionError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 401

    data = request.get_json(silent=True) or {}
    sets, vals = [], []

    if 'is_playing' in data:
        sets.append('is_playing=%s'); vals.append(bool(data['is_playing']))
    if 'volume' in data:
        volume = max(0, min(100, int(data['volume'])))
        sets.append('volume=%s'); vals.append(volume)
    if 'position_ms' in data:
        sets.append('position_ms=%s'); vals.append(max(0, int(data['position_ms'] or 0)))
    if 'duration_ms' in data:
        sets.append('duration_ms=%s'); vals.append(max(0, int(data['duration_ms'] or 0)))
    if 'seek_position_ms' in data:
        sets.append('seek_position_ms=%s'); vals.append(max(0, int(data['seek_position_ms'] or 0)))
        sets.append('seek_revision=seek_revision+1')

    if not sets:
        return jsonify({'ok': False, 'error': 'Estado inválido.'}), 400

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                '''INSERT INTO music_player_state (broadcaster_user_id) VALUES (%s)
                   ON CONFLICT (broadcaster_user_id) DO NOTHING''',
                (int(broadcaster_id),)
            )
            # UPDATE ... RETURNING elimina uma segunda ida ao PostgreSQL.
            # Play/Pause/seek/volume são comandos quentes e não precisam
            # reconstruir o snapshot completo do player.
            cur.execute(
                f'''UPDATE music_player_state
                       SET {", ".join(sets)}, updated_at=NOW()
                     WHERE broadcaster_user_id=%s
                 RETURNING current_queue_id,is_playing,volume,position_ms,
                           duration_ms,seek_position_ms,seek_revision''',
                [*vals, int(broadcaster_id)]
            )
            row = cur.fetchone()

        conn.commit()
    finally:
        conn.close()

    state = {
        'current_queue_id': row[0] if row else None,
        'is_playing': bool(row[1]) if row else False,
        'volume': int(row[2]) if row else 80,
        'position_ms': max(0, int(row[3] or 0)) if row else 0,
        'duration_ms': max(0, int(row[4] or 0)) if row else 0,
        'seek_position_ms': max(0, int(row[5] or 0)) if row else 0,
        'seek_revision': int(row[6] or 0) if row else 0,
    }
    return jsonify({'ok': True, 'state': state})


DIRECT_AUDIO_EXTENSIONS = ('.mp3', '.m4a', '.aac', '.ogg', '.wav', '.opus')


def _is_direct_audio_url(url):
    from urllib.parse import urlparse
    return urlparse(str(url or '').strip()).path.lower().endswith(DIRECT_AUDIO_EXTENSIONS)

def _spotify_track_id(url):
    value = str(url or '').strip()
    if value.startswith('spotify:track:'):
        track_id = value.split(':', 2)[2].strip()
        return track_id if len(track_id) == 22 else None
    try:
        from urllib.parse import urlparse
        parsed = urlparse(value)
        if parsed.netloc.lower() in {'open.spotify.com', 'play.spotify.com'}:
            parts = [part for part in parsed.path.split('/') if part]
            if len(parts) >= 2 and parts[0].lower() == 'track':
                track_id = parts[1].split('?')[0].strip()
                return track_id if len(track_id) == 22 else None
    except Exception:
        pass
    return None




@music_bp.post('/<int:broadcaster_id>/queue')
def add_music(broadcaster_id):
    try:
        require_session_broadcaster(broadcaster_id)
    except PermissionError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 401
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
    if provider in {'soundcloud', 'unknown'}:
        return jsonify({'ok': False, 'error': 'Esta fonte ainda não é compatível com o player do OBS. Use YouTube, Spotify ou um link direto de áudio.'}), 422
    if provider == 'spotify' and not _spotify_track_id(source_url):
        return jsonify({'ok': False, 'error': 'Link do Spotify inválido. Use um link de faixa do Spotify.'}), 422
    if provider == 'link' and not _is_direct_audio_url(source_url):
        return jsonify({'ok': False, 'error': 'Para links, use uma URL direta de áudio (.mp3, .m4a, .aac, .ogg, .wav ou .opus).'}), 422
    settings = _settings(broadcaster_id)
    if provider == 'youtube' and not settings['allow_youtube']:
        return jsonify({'ok': False, 'error': 'YouTube está desativado nas fontes do canal.'}), 403
    if provider == 'spotify' and not settings['allow_spotify']:
        return jsonify({'ok': False, 'error': 'Spotify está desativado nas fontes do canal.'}), 403
    if provider == 'soundcloud' and not settings['allow_soundcloud']:
        return jsonify({'ok': False, 'error': 'SoundCloud está desativado nas fontes do canal.'}), 403
    if source_url and not settings['allow_links']:
        return jsonify({'ok': False, 'error': 'Links estão desativados nas fontes do canal.'}), 403

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"sn7-music-queue:{int(broadcaster_id)}",))
            if _queue_duplicate_exists(cur, broadcaster_id, provider, source_url, title, artist):
                return jsonify({'ok': False, 'error': f'🎵 "{title}" já está na fila.'}), 409
            cur.execute("SELECT COUNT(*) FROM music_queue WHERE broadcaster_user_id=%s AND status='queued'", (int(broadcaster_id),))
            if int(cur.fetchone()[0] or 0) >= 100:
                return jsonify({'ok': False, 'error': 'A fila deste canal já atingiu o limite de 100 músicas.'}), 409
            cur.execute('SELECT COALESCE(MAX(position),0)+1 FROM music_queue WHERE broadcaster_user_id=%s AND status=\'queued\'', (int(broadcaster_id),))
            position = int(cur.fetchone()[0] or 1)
            cur.execute('''
                INSERT INTO music_queue (broadcaster_user_id, provider, title, artist, source_url, added_by, status, position)
                VALUES (%s,%s,%s,%s,%s,%s,'queued',%s) RETURNING id
            ''', (int(broadcaster_id), provider, title, artist, source_url, added_by, position))
            item_id = cur.fetchone()[0]
            # Primeira música: vira a atual e inicia automaticamente.
            cur.execute(
                "INSERT INTO music_player_state (broadcaster_user_id,current_queue_id,is_playing) "
                "VALUES (%s,%s,TRUE) "
                "ON CONFLICT (broadcaster_user_id) DO UPDATE SET "
                "current_queue_id=COALESCE(music_player_state.current_queue_id,EXCLUDED.current_queue_id), "
                "is_playing=CASE WHEN music_player_state.current_queue_id IS NULL THEN TRUE ELSE music_player_state.is_playing END, "
                "updated_at=NOW()",
                (int(broadcaster_id), item_id),
            )
        conn.commit()
    finally:
        conn.close()
    invalidate_queue_cache(broadcaster_id)
    return jsonify({
        'ok': True,
        'added_id': item_id,
        'item': {
            'id': item_id,
            'provider': provider,
            'title': title,
            'artist': artist,
            'added_by': added_by,
            'position': position,
        },
    })


@music_bp.post('/<int:broadcaster_id>/queue/<int:item_id>/remove')
def remove_music(broadcaster_id, item_id):
    try:
        require_session_broadcaster(broadcaster_id)
    except PermissionError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 401
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('''DELETE FROM music_queue WHERE id=%s AND broadcaster_user_id=%s AND status='queued' ''',
                        (item_id, int(broadcaster_id)))
        conn.commit()
    finally:
        conn.close()
    invalidate_queue_cache(broadcaster_id)
    return jsonify({'ok': True, 'removed_id': int(item_id)})


@music_bp.post('/<int:broadcaster_id>/skip')
def skip_music(broadcaster_id):
    try:
        require_session_broadcaster(broadcaster_id)
    except PermissionError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 401
    try:
        data = skip_current_fast(broadcaster_id)
        invalidate_queue_cache(broadcaster_id)
        return jsonify(data)
    except Exception as exc:
        print(f'[MUSIC] skip erro: {exc}', flush=True)
        return jsonify({'ok': False, 'error': str(exc)}), 500


@music_bp.post('/<int:broadcaster_id>/previous')
def previous_music(broadcaster_id):
    try:
        require_session_broadcaster(broadcaster_id)
    except PermissionError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 401
    try:
        current = previous_current(broadcaster_id)
        if not current:
            return jsonify({'ok': False, 'error': 'Não existe uma música anterior disponível.'}), 409
        return jsonify(snapshot(broadcaster_id))
    except Exception as exc:
        print(f'[MUSIC] previous erro: {exc}', flush=True)
        return jsonify({'ok': False, 'error': str(exc)}), 500



@music_bp.post('/<int:broadcaster_id>/queue/select')
def select_music(broadcaster_id):
    try:
        require_session_broadcaster(broadcaster_id)
    except PermissionError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 401
    data = request.get_json(silent=True) or {}
    try:
        queue_id = int(data.get('queue_id') or 0)
    except (TypeError, ValueError):
        queue_id = 0
    if queue_id <= 0:
        return jsonify({'ok': False, 'error': 'Música inválida.'}), 400
    try:
        current = select_current(broadcaster_id, queue_id)
        if not current:
            return jsonify({'ok': False, 'error': 'Essa música não está mais na fila.'}), 409
        return jsonify(snapshot(broadcaster_id))
    except Exception as exc:
        print(f'[MUSIC] select erro: {exc}', flush=True)
        return jsonify({'ok': False, 'error': str(exc)}), 500



@music_bp.post('/<int:broadcaster_id>/queue/clear')
def clear_music(broadcaster_id):
    try:
        require_session_broadcaster(broadcaster_id)
    except PermissionError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 401
    # Usa o mesmo caminho do motor do player para limpar a fila e,
    # principalmente, zerar current_queue_id/is_playing no mesmo reset.
    clear_queue(broadcaster_id)
    invalidate_queue_cache(broadcaster_id)
    return jsonify({'ok': True, 'queue': [], 'current': None})


# ---------------------------------------------------------------------------
# SN7 MUSIC OAUTH
# ---------------------------------------------------------------------------
MUSIC_PROVIDERS = {
    "youtube": {
        "label": "YouTube",
        "client_id_env": "YOUTUBE_CLIENT_ID",
        "client_secret_env": "YOUTUBE_CLIENT_SECRET",
        "redirect_env": "YOUTUBE_REDIRECT_URI",
        "authorize": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "scope": "https://www.googleapis.com/auth/youtube.force-ssl",
    },
    "spotify": {
        "label": "Spotify",
        "client_id_env": "SPOTIFY_CLIENT_ID",
        "client_secret_env": "SPOTIFY_CLIENT_SECRET",
        "redirect_env": "SPOTIFY_REDIRECT_URI",
        "authorize": "https://accounts.spotify.com/authorize",
        "token": "https://accounts.spotify.com/api/token",
        "scope": "streaming user-read-private user-read-email user-read-playback-state user-modify-playback-state user-read-currently-playing",
    },
    "soundcloud": {
        "label": "SoundCloud",
        "client_id_env": "SOUNDCLOUD_CLIENT_ID",
        "client_secret_env": "SOUNDCLOUD_CLIENT_SECRET",
        "redirect_env": "SOUNDCLOUD_REDIRECT_URI",
        "authorize": "https://secure.soundcloud.com/authorize",
        "token": "https://secure.soundcloud.com/oauth/token",
        "scope": "",
    },
}


def _provider_config(provider):
    return MUSIC_PROVIDERS.get(str(provider or "").lower())


def _oauth_redirect_uri(provider):
    cfg = _provider_config(provider)
    configured = os.environ.get(cfg["redirect_env"], "").strip() if cfg else ""

    # O redirect URI precisa ser absolutamente idêntico ao cadastrado no provedor.
    # O domínio público atual do SN7 é sn7core.com. Algumas instalações antigas
    # ainda carregam o endereço legado do Render nas variáveis de ambiente; para
    # Spotify isso causava "redirect_uri: Not matching configuration".
    if configured.startswith("https://") and "/api/music/callback/" in configured:
        configured = configured.rstrip("/")
        if provider == "spotify" and "sn7-core.onrender.com" in configured:
            return "https://sn7core.com/api/music/callback/spotify"
        return configured

    public_url = os.environ.get("SN7_PUBLIC_URL", "https://sn7core.com").strip().rstrip("/")
    if "sn7-core.onrender.com" in public_url:
        public_url = "https://sn7core.com"
    return f"{public_url}/api/music/callback/{provider}"


def _pkce_pair():
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _music_oauth_error(message, status=400):
    if "text/html" in str(request.headers.get("Accept") or ""):
        safe = str(message).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;")
        return f'''<!doctype html><html lang="pt-BR"><meta charset="utf-8"><title>SN7 • Conexão</title>
<style>body{{margin:0;background:#0b0d12;color:#e8ebf2;font:16px system-ui;display:grid;place-items:center;min-height:100vh;padding:24px}}main{{max-width:520px;background:#121720;border:1px solid #2a3240;border-radius:18px;padding:24px}}a{{display:inline-block;margin-top:18px;color:#fff;background:#1c2430;border:1px solid #354052;border-radius:10px;padding:10px 14px;text-decoration:none;font-weight:700}}</style>
<main><h2>Conexão não iniciada</h2><p>{safe}</p><a href="/dashboard">Voltar ao painel</a></main></html>''', status
    return jsonify({"ok": False, "error": message}), status


def _save_music_connection(bid, provider, profile, token):
    now = int(time.time())
    expires_in = int(token.get("expires_in") or 0)
    expires_at = now + expires_in if expires_in else 0
    profile = profile or {}
    external_id = str(profile.get("id") or profile.get("sub") or profile.get("urn") or "")
    username = str(profile.get("username") or profile.get("login") or "").strip()
    display_name = str(
        profile.get("display_name")
        or profile.get("name")
        or profile.get("title")
        or username
        or "Conta conectada"
    ).strip()
    profile_url = str(
        profile.get("profile_url")
        or profile.get("external_urls", {}).get("spotify")
        or profile.get("permalink_url")
        or ""
    ).strip()
    images = profile.get("images") or []
    avatar_url = str(
        profile.get("avatar_url")
        or (images[0].get("url") if isinstance(images, list) and images and isinstance(images[0], dict) else "")
        or profile.get("thumbnail_url")
        or ""
    ).strip()

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO music_connections
                    (broadcaster_user_id,provider,external_user_id,username,display_name,
                     profile_url,avatar_url,access_token,refresh_token,expires_at,scope,updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                ON CONFLICT (broadcaster_user_id,provider) DO UPDATE SET
                    external_user_id=EXCLUDED.external_user_id,
                    username=EXCLUDED.username,
                    display_name=EXCLUDED.display_name,
                    profile_url=EXCLUDED.profile_url,
                    avatar_url=EXCLUDED.avatar_url,
                    access_token=EXCLUDED.access_token,
                    refresh_token=COALESCE(EXCLUDED.refresh_token,music_connections.refresh_token),
                    expires_at=EXCLUDED.expires_at,
                    scope=EXCLUDED.scope,
                    updated_at=NOW()
                """,
                (
                    int(bid), provider, external_id, username, display_name,
                    profile_url, avatar_url, token.get("access_token"), token.get("refresh_token"),
                    expires_at, str(token.get("scope") or "")
                ),
            )
        # IMPORTANTE: o OAuth do Music Player é separado do OAuth do bot de chat.
        # Não copie o token de música para chat_connections, pois isso sobrescreveria
        # a credencial usada pelo bot do YouTube.
        conn.commit()
    finally:
        conn.close()


def _music_connections(bid):
    configured = {
        provider: bool(os.environ.get(cfg["client_id_env"], "").strip()
                       and os.environ.get(cfg["client_secret_env"], "").strip())
        for provider, cfg in MUSIC_PROVIDERS.items()
    }
    result = {
        provider: {
            "configured": configured[provider],
            "connected": False,
            "display_name": "",
            "username": "",
            "profile_url": "",
            "avatar_url": "",
            "expires_at": 0,
        }
        for provider in MUSIC_PROVIDERS
    }
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT provider,display_name,username,profile_url,avatar_url,expires_at
                FROM music_connections
                WHERE broadcaster_user_id=%s
                """,
                (int(bid),),
            )
            for provider, display_name, username, profile_url, avatar_url, expires_at in cur.fetchall():
                if provider not in result:
                    continue
                result[provider].update({
                    "connected": True,
                    "display_name": display_name or "",
                    "username": username or "",
                    "profile_url": profile_url or "",
                    "avatar_url": avatar_url or "",
                    "expires_at": int(expires_at or 0),
                })
    finally:
        conn.close()
    return result


def _fetch_profile(provider, access_token):
    headers = {"Accept": "application/json"}
    if provider == "youtube":
        response = requests.get(
            "https://www.googleapis.com/youtube/v3/channels",
            params={"part": "snippet", "mine": "true", "maxResults": 1},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=12,
        )
        data = response.json()
        if response.status_code >= 400:
            raise RuntimeError(data.get("error", {}).get("message") or "YouTube recusou o acesso.")
        item = (data.get("items") or [None])[0]
        if not item:
            raise RuntimeError("A conta Google não possui um canal do YouTube disponível.")
        snippet = item.get("snippet") or {}
        thumbnails = snippet.get("thumbnails") or {}
        avatar_url = ""
        for size in ("high", "medium", "default"):
            candidate = thumbnails.get(size) or {}
            if candidate.get("url"):
                avatar_url = str(candidate["url"]).strip()
                break
        return {
            "id": item.get("id"),
            "name": snippet.get("title"),
            "profile_url": f"https://www.youtube.com/channel/{item.get('id')}",
            "avatar_url": avatar_url,
        }

    if provider == "spotify":
        response = requests.get(
            "https://api.spotify.com/v1/me",
            headers={"Authorization": f"Bearer {access_token}", **headers},
            timeout=12,
        )
        data = response.json()
        if response.status_code >= 400:
            raise RuntimeError(data.get("error", {}).get("message") or "Spotify recusou o acesso.")

        # Normaliza a foto do perfil do Spotify para o mesmo campo usado
        # pelo YouTube/SoundCloud e pelo painel.
        images = data.get("images") or []
        avatar_url = ""
        if isinstance(images, list):
            for image in images:
                if isinstance(image, dict) and image.get("url"):
                    avatar_url = str(image["url"]).strip()
                    break
        data["avatar_url"] = avatar_url
        return data

    if provider == "soundcloud":
        response = requests.get(
            "https://api.soundcloud.com/me",
            headers={"Authorization": f"OAuth {access_token}", **headers},
            timeout=12,
        )
        data = response.json()
        if response.status_code >= 400:
            raise RuntimeError(data.get("message") or data.get("error") or "SoundCloud recusou o acesso.")
        return data

    raise RuntimeError("Provedor não suportado.")


def _exchange_music_code(provider, code, verifier, redirect_uri):
    cfg = _provider_config(provider)
    client_id = os.environ.get(cfg["client_id_env"], "").strip()
    client_secret = os.environ.get(cfg["client_secret_env"], "").strip()
    payload = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "code": code,
    }
    if verifier and provider in {"spotify", "soundcloud"}:
        payload["code_verifier"] = verifier

    headers = {"Accept": "application/json"}
    if provider == "spotify":
        from base64 import b64encode
        headers["Authorization"] = "Basic " + b64encode(
            f"{client_id}:{client_secret}".encode()
        ).decode()
        payload.pop("client_secret", None)

    response = requests.post(cfg["token"], data=payload, headers=headers, timeout=15)
    data = response.json()
    if response.status_code >= 400 or not data.get("access_token"):
        detail = data.get("error_description") or data.get("error") or "troca do código recusada"
        raise RuntimeError(f"{cfg['label']}: {detail}")
    return data


@music_bp.get("/<int:broadcaster_id>/spotify/player-token")
def get_spotify_player_token(broadcaster_id):
    """Entrega ao player web um token Spotify já renovado, sem expor refresh_token."""
    try:
        require_session_broadcaster(broadcaster_id)
    except PermissionError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 401

    try:
        token = _spotify_access_token(broadcaster_id)
        if not token:
            return jsonify({"ok": False, "error": "Spotify não está conectado neste canal."}), 404

        # O navegador usa um cache curto em memória para não pedir o token ao
        # SN7 a cada Play. O valor é apenas uma dica de TTL.
        return jsonify({"ok": True, "token": token, "expires_in": 240})
    except Exception as exc:
        print(f"[MUSIC-SPOTIFY] token do player falhou: {exc}", flush=True)
        return jsonify({"ok": False, "error": "Não foi possível preparar o player do Spotify."}), 502


@music_bp.get("/<int:broadcaster_id>/connections")
def get_music_connections(broadcaster_id):
    try:
        response = jsonify({"ok": True, "connections": _music_connections(broadcaster_id)})
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return response
    except Exception as exc:
        print(f"[MUSIC-OAUTH] status erro: {exc}", flush=True)
        return jsonify({"ok": False, "error": "Não foi possível consultar as conexões."}), 500


@music_bp.get("/<int:broadcaster_id>/connect/<provider>")
def connect_music_provider(broadcaster_id, provider):
    provider = str(provider or "").lower()
    cfg = _provider_config(provider)
    if not cfg:
        return _music_oauth_error("Plataforma não suportada.", 404)
    try:
        require_session_broadcaster(broadcaster_id)
    except PermissionError as exc:
        return _music_oauth_error(str(exc), 401)

    client_id = os.environ.get(cfg["client_id_env"], "").strip()
    client_secret = os.environ.get(cfg["client_secret_env"], "").strip()

    print(
        f"[MUSIC-OAUTH] {provider}: "
        f"client_id={"OK" if client_id else "MISSING"}, "
        f"client_secret={"OK" if client_secret else "MISSING"}, "
        f"redirect_env={"OK" if os.environ.get(cfg["redirect_env"], "").strip() else "MISSING"}",
        flush=True,
    )

    if not client_id or not client_secret:
        missing = []
        if not client_id:
            missing.append(cfg["client_id_env"])
        if not client_secret:
            missing.append(cfg["client_secret_env"])
        return _music_oauth_error(
            f"{cfg['label']} ainda não está configurado no Render. Variável(veis) ausente(s): {', '.join(missing)}.",
            503,
        )

    state = secrets.token_urlsafe(32)
    verifier, challenge = _pkce_pair()
    session_key = f"music_oauth_{provider}"
    session[session_key] = {
        "state": state,
        "verifier": verifier,
        "broadcaster_id": int(broadcaster_id),
        "created_at": int(time.time()),
    }

    redirect_uri = _oauth_redirect_uri(provider)
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    if cfg["scope"]:
        params["scope"] = cfg["scope"]

    if provider == "youtube":
        params.update({
            "access_type": "offline",
            "include_granted_scopes": "true",
            "prompt": "consent",
        })
    elif provider == "spotify":
        params.update({
            "show_dialog": "true",
        })
    elif provider == "soundcloud":
        params.update({
            "code_challenge_method": "S256",
            "code_challenge": challenge,
        })

    return redirect(f"{cfg['authorize']}?{urlencode(params)}")


@music_bp.get("/callback/<provider>")
def music_provider_callback(provider):
    provider = str(provider or "").lower()
    cfg = _provider_config(provider)
    if not cfg:
        return _music_oauth_error("Plataforma não suportada.", 404)

    state_data = session.pop(f"music_oauth_{provider}", None)
    broadcaster_id = int(state_data.get("broadcaster_id") or 0) if state_data else 0
    state_age = int(time.time()) - int((state_data or {}).get("created_at") or 0)
    if not state_data or broadcaster_id <= 0 or state_age > 600:
        return _music_oauth_error("A sessão do OAuth expirou. Inicie a conexão novamente.", 400)
    if not secrets.compare_digest(str(request.args.get("state") or ""), str(state_data.get("state") or "")):
        return _music_oauth_error("OAuth state inválido.", 400)
    if request.args.get("error"):
        return _music_oauth_error(
            f"{cfg['label']}: {request.args.get('error_description') or request.args.get('error')}",
            400,
        )
    code = str(request.args.get("code") or "").strip()
    if not code:
        return _music_oauth_error("O provedor não retornou o código de autorização.", 400)

    try:
        require_session_broadcaster(broadcaster_id)
        redirect_uri = _oauth_redirect_uri(provider)
        token = _exchange_music_code(provider, code, state_data.get("verifier"), redirect_uri)
        profile = _fetch_profile(provider, token["access_token"])
        _save_music_connection(broadcaster_id, provider, profile, token)
        return redirect("/dashboard?music_connected=" + provider)
    except Exception as exc:
        print(f"[MUSIC-OAUTH] callback {provider} falhou: {exc}", flush=True)
        return _music_oauth_error(f"Não foi possível conectar ao {cfg['label']}: {exc}", 502)


@music_bp.post("/<int:broadcaster_id>/disconnect/<provider>")
def disconnect_music_provider(broadcaster_id, provider):
    try:
        require_session_broadcaster(broadcaster_id)
    except PermissionError as exc:
        return _music_oauth_error(str(exc), 401)
    provider = str(provider or "").lower()
    if provider not in MUSIC_PROVIDERS:
        return _music_oauth_error("Plataforma não suportada.", 404)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM music_connections WHERE broadcaster_user_id=%s AND provider=%s",
                (int(broadcaster_id), provider),
            )
        conn.commit()
    finally:
        conn.close()
    response = jsonify({"ok": True, "connections": _music_connections(broadcaster_id), "disconnected": provider})
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response
