import os
from flask import Flask, jsonify, render_template, request, redirect, url_for, session
from core.database import init_db
from routes.economy import economy_bp
from routes.ranking import ranking_bp
from routes.duel import duel_bp
from routes.commands import commands_bp
from routes.settings import settings_bp
from routes.kick import kick_bp

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-me-in-production")

app.register_blueprint(economy_bp, url_prefix="/api/economy")
app.register_blueprint(ranking_bp, url_prefix="/api/ranking")
app.register_blueprint(duel_bp, url_prefix="/api/duel")
app.register_blueprint(commands_bp, url_prefix="/api/commands")
app.register_blueprint(settings_bp, url_prefix="/api/settings")
app.register_blueprint(kick_bp, url_prefix="/kick")

@app.before_request
def ensure_db():
    if os.getenv("DATABASE_URL"):
        init_db()

@app.get("/")
def home():
    return render_template("login.html")

@app.get("/dashboard")
def dashboard():
    broadcaster_id = session.get("broadcaster_id")
    if not broadcaster_id:
        # Development fallback; production should require Kick OAuth.
        broadcaster_id = request.args.get("broadcaster_id")
    if not broadcaster_id:
        return redirect(url_for("home"))
    return render_template("dashboard.html", broadcaster_id=broadcaster_id)

@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "SN7 Core API", "version": "1.0.0"})

@app.get("/api")
def api_info():
    return jsonify({
        "ok": True,
        "service": "SN7 Core API",
        "modules": ["kick", "economy", "ranking", "duel", "commands", "settings"],
        "v_d": False
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
