from flask import Blueprint, jsonify, request
from core.services import ensure_channel

kick_bp = Blueprint("kick", __name__)

@kick_bp.get("/login")
def login():
    return jsonify({
        "ok": False,
        "status": "pending",
        "message": "OAuth real da Kick será migrado do Worker atual."
    }), 501

@kick_bp.post("/webhook")
def webhook():
    payload = request.get_json(silent=True) or {}
    broadcaster_id = payload.get("broadcaster_user_id")
    if broadcaster_id:
        ensure_channel(broadcaster_id, payload.get("broadcaster_username", ""))
    return jsonify({"ok": True})
