import base64
import hashlib
import hmac
import json
import os
import time
from urllib.parse import quote

from flask import Blueprint, jsonify, render_template, request

from core.auth import require_session_broadcaster
from core.database import get_conn
from routes.store import _resolve_channel

overlay_bp = Blueprint("overlay", __name__)


def _secret():
    return (os.environ.get("FLASK_SECRET_KEY") or os.environ.get("KICK_CLIENT_SECRET") or "sn7-overlay-secret").encode("utf-8")


def make_overlay_token(broadcaster_id):
    body = base64.urlsafe_b64encode(f"{int(broadcaster_id)}:sn7-overlay-v1".encode()).rstrip(b"=").decode()
    sig = hmac.new(_secret(), body.encode(), hashlib.sha256).digest()
    return body + "." + base64.urlsafe_b64encode(sig).rstrip(b"=").decode()


def validate_overlay_token(token, broadcaster_id):
    try:
        body, sig = str(token or "").split(".", 1)
        expected = hmac.new(_secret(), body.encode(), hashlib.sha256).digest()
        supplied = base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4))
        if not hmac.compare_digest(expected, supplied):
            return False
        payload = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode()
        bid, marker = payload.split(":", 1)
        return int(bid) == int(broadcaster_id) and marker == "sn7-overlay-v1"
    except Exception:
        return False


def _public_url():
    return os.environ.get("SN7_PUBLIC_URL", "https://sn7core.com").strip().rstrip("/").replace("https://sn7-core.onrender.com", "https://sn7core.com")


def _default_config():
    return {
        "version": 1,
        "canvas": {"width": 1920, "height": 1080},
        "elements": [
            {"id": "store-alert", "type": "store_alert", "name": "Alertas da Loja", "x": 1440, "y": 820, "width": 400, "height": 120, "enabled": True,
             "settings": {"duration": 5, "text": "{viewer} resgatou {item}"}},
            {"id": "store-audio", "type": "store_audio", "name": "Áudios da Loja", "x": 760, "y": 840, "width": 400, "height": 120, "enabled": True,
             "settings": {"volume": 1}},
            {"id": "music", "type": "music", "name": "Música", "x": 760, "y": 40, "width": 400, "height": 80, "enabled": True,
             "settings": {"show": True}},
        ],
    }


def _load_config(bid):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT config FROM overlay_configs WHERE broadcaster_user_id=%s", (int(bid),))
            row = cur.fetchone()
        return row[0] if row and row[0] else _default_config()
    finally:
        conn.close()


def _save_config(bid, config):
    # Keep the stored document deliberately small and predictable.
    if not isinstance(config, dict):
        raise ValueError("Configuração inválida.")
    elements = config.get("elements")
    if not isinstance(elements, list) or len(elements) > 30:
        raise ValueError("O overlay pode ter até 30 elementos.")
    clean = {"version": 1, "canvas": {"width": 1920, "height": 1080}, "elements": []}
    allowed_types = {"store_alert", "store_audio", "music", "text", "image"}
    for idx, e in enumerate(elements):
        if not isinstance(e, dict):
            continue
        typ = str(e.get("type") or "text")
        if typ not in allowed_types:
            continue
        clean["elements"].append({
            "id": str(e.get("id") or f"element-{idx}")[:80],
            "type": typ,
            "name": str(e.get("name") or typ)[:80],
            "x": max(0, min(1920, float(e.get("x", 0)))),
            "y": max(0, min(1080, float(e.get("y", 0)))),
            "width": max(40, min(1920, float(e.get("width", 300)))),
            "height": max(30, min(1080, float(e.get("height", 100)))),
            "enabled": bool(e.get("enabled", True)),
            "settings": e.get("settings") if isinstance(e.get("settings"), dict) else {},
        })
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO overlay_configs(broadcaster_user_id,config,updated_at)
                           VALUES(%s,%s::jsonb,NOW())
                           ON CONFLICT (broadcaster_user_id)
                           DO UPDATE SET config=EXCLUDED.config,updated_at=NOW()""",
                        (int(bid), json.dumps(clean, ensure_ascii=False)))
        conn.commit()
    finally:
        conn.close()
    return clean


def _overlay_url(bid):
    channel = _resolve_channel(str(bid)) or {"username": str(bid)}
    target = str(channel.get("username") or bid)
    token = make_overlay_token(bid)
    return f"{_public_url()}/overlay/{quote(target, safe='')}?token={quote(token, safe='')}"


@overlay_bp.get("/api/<int:broadcaster_id>/config")
@overlay_bp.get("/api/overlay/<int:broadcaster_id>/config")
def get_config(broadcaster_id):
    require_session_broadcaster(broadcaster_id)
    return jsonify({"ok": True, "config": _load_config(broadcaster_id), "overlay_url": _overlay_url(broadcaster_id)})


@overlay_bp.put("/api/<int:broadcaster_id>/config")
@overlay_bp.put("/api/overlay/<int:broadcaster_id>/config")
def put_config(broadcaster_id):
    require_session_broadcaster(broadcaster_id)
    data = request.get_json(silent=True) or {}
    try:
        config = _save_config(broadcaster_id, data.get("config", data))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "config": config, "overlay_url": _overlay_url(broadcaster_id)})


@overlay_bp.get("/api/<target>/events")
def events(target):
    channel = _resolve_channel(target)
    if not channel:
        return jsonify({"ok": False, "error": "Overlay/canal não encontrado."}), 404
    if not validate_overlay_token(request.args.get("token"), channel["broadcaster_user_id"]):
        return jsonify({"ok": False, "error": "Overlay não autorizado."}), 401
    try:
        after = max(0, int(request.args.get("after", 0)))
    except (TypeError, ValueError):
        after = 0
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT id,event_type,payload,created_at
                             FROM overlay_events
                            WHERE broadcaster_user_id=%s AND id>%s
                            ORDER BY id ASC LIMIT 50""",
                        (int(channel["broadcaster_user_id"]), after))
            rows = cur.fetchall()
    finally:
        conn.close()
    return jsonify({"ok": True, "events": [
        {"id": int(r[0]), "type": r[1], "payload": r[2], "created_at": r[3].isoformat()}
        for r in rows
    ]})


@overlay_bp.get("/api/<int:broadcaster_id>/url")
@overlay_bp.get("/api/overlay/<int:broadcaster_id>/url")
def overlay_url(broadcaster_id):
    require_session_broadcaster(broadcaster_id)
    return jsonify({"ok": True, "overlay_url": _overlay_url(broadcaster_id)})


def emit_overlay_event(broadcaster_id, event_type, payload):
    """Publica um evento pequeno para o Overlay; falha não afeta a ação original."""
    try:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""INSERT INTO overlay_events(broadcaster_user_id,event_type,payload)
                               VALUES(%s,%s,%s::jsonb) RETURNING id""",
                            (int(broadcaster_id), str(event_type)[:60],
                             json.dumps(payload or {}, ensure_ascii=False)))
                event_id = int(cur.fetchone()[0])
                cur.execute("""DELETE FROM overlay_events
                                WHERE broadcaster_user_id=%s
                                  AND id < (SELECT COALESCE(MAX(id),0)-250 FROM overlay_events WHERE broadcaster_user_id=%s)""",
                            (int(broadcaster_id), int(broadcaster_id)))
            conn.commit()
        finally:
            conn.close()
        return event_id
    except Exception as exc:
        print(f"[OVERLAY] evento não publicado: {exc}", flush=True)
        return None
