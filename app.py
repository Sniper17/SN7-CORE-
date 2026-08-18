from flask import Flask, jsonify, render_template, request
from core.database import init_db, get_conn
from routes.economy import economy_bp
from routes.ranking import ranking_bp
from routes.duel import duel_bp
from routes.commands import commands_bp
from routes.settings import settings_bp
from routes.kick import kick_bp
import os

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change-me")

app.register_blueprint(economy_bp, url_prefix="/api/economy")
app.register_blueprint(ranking_bp, url_prefix="/api/ranking")
app.register_blueprint(duel_bp, url_prefix="/api/duel")
app.register_blueprint(commands_bp, url_prefix="/api/commands")
app.register_blueprint(settings_bp, url_prefix="/api/settings")
app.register_blueprint(kick_bp, url_prefix="/kick")


@app.before_request
def database_bootstrap():
    if os.environ.get("DATABASE_URL"):
        init_db()


def _dashboard_broadcaster_id():
    requested = str(request.args.get("broadcaster_id") or "").strip()
    if requested.isdigit():
        return requested

    # Sem broadcaster_id na URL, usa o canal Kick conectado mais recentemente.
    # Isso evita o painel operar no ID fictício "1" enquanto o webhook usa o ID real.
    if os.environ.get("DATABASE_URL"):
        try:
            conn = get_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT broadcaster_user_id
                          FROM kick_connections
                         ORDER BY updated_at DESC
                         LIMIT 1
                        """
                    )
                    row = cur.fetchone()
                    if row:
                        return str(row[0])
            finally:
                conn.close()
        except Exception as exc:
            print(f"[DASHBOARD] não foi possível resolver canal conectado: {exc}", flush=True)

    return "1"


@app.get("/")
def home():
    return render_template("dashboard.html", broadcaster_id=_dashboard_broadcaster_id())


@app.get("/dashboard")
def dashboard():
    return render_template("dashboard.html", broadcaster_id=_dashboard_broadcaster_id())


@app.get("/health")
def health():
    return jsonify({
        "ok": True,
        "service": "SN7 Core API",
        "version": "1.4.0",
        "database_configured": bool(os.environ.get("DATABASE_URL")),
        "kick_webhook": True,
        "v_d": False
    })


@app.get("/api")
def api():
    return jsonify({
        "ok": True,
        "service": "SN7 Core API",
        "version": "1.4.0",
        "multi_streamer": True,
        "v_d": False,
        "database_configured": bool(os.environ.get("DATABASE_URL")),
        "modules": [
            "economy",
            "ranking",
            "duel",
            "commands",
            "settings",
            "kick"
        ]
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
