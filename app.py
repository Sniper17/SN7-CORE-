from flask import Flask, jsonify, render_template, request, redirect
from core.database import init_db, get_conn
from core.auth import get_session_broadcaster_id, require_session_broadcaster
from routes.economy import economy_bp
from routes.ranking import ranking_bp
from routes.duel import duel_bp
from routes.commands import commands_bp
from routes.settings import settings_bp
from routes.kick import kick_bp
from routes.music import music_bp
from routes.obs import obs_bp
import os
import re

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change-me")

app.register_blueprint(economy_bp, url_prefix="/api/economy")
app.register_blueprint(ranking_bp, url_prefix="/api/ranking")
app.register_blueprint(duel_bp, url_prefix="/api/duel")
app.register_blueprint(commands_bp, url_prefix="/api/commands")
app.register_blueprint(settings_bp, url_prefix="/api/settings")
app.register_blueprint(kick_bp, url_prefix="/kick")
app.register_blueprint(music_bp, url_prefix="/api/music")
app.register_blueprint(obs_bp)


@app.before_request
def enforce_session_channel():
    match = re.match(r"^/api/(economy|ranking|duel|commands|settings|music|obs)/(\d+)(?:/|$)", request.path)
    if not match:
        return None
    try:
        require_session_broadcaster(int(match.group(2)))
    except PermissionError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 403
    return None


@app.before_request
def database_bootstrap():
    if os.environ.get("DATABASE_URL"):
        init_db()


def _dashboard_broadcaster_id():
    current = get_session_broadcaster_id()
    return str(current) if current is not None else None


@app.get("/")
def home():
    # O painel é público. O login fica somente no perfil.
    return render_template(
        "dashboard.html",
        broadcaster_id=_dashboard_broadcaster_id(),
        public_mode=_dashboard_broadcaster_id() is None,
    )


@app.get("/dashboard")
def dashboard():
    return render_template(
        "dashboard.html",
        broadcaster_id=_dashboard_broadcaster_id(),
        public_mode=_dashboard_broadcaster_id() is None,
    )


@app.get("/perfil")
def profile():
    return redirect("/?profile=1")


@app.get("/health")
def health():
    return jsonify({
        "ok": True,
        "service": "SN7 Core API",
        "version": "1.6.0",
        "database_configured": bool(os.environ.get("DATABASE_URL")),
        "kick_webhook": True,
        "v_d": False
    })


@app.get("/api")
def api():
    return jsonify({
        "ok": True,
        "service": "SN7 Core API",
        "version": "1.6.0",
        "multi_streamer": True,
        "v_d": False,
        "database_configured": bool(os.environ.get("DATABASE_URL")),
        "modules": [
            "economy",
            "ranking",
            "duel",
            "commands",
            "settings",
            "music",
            "obs",
            "kick"
        ]
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
