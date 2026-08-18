from flask import Flask, jsonify, render_template, request
from core.database import init_db
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

@app.get("/")
def home():
    return render_template("dashboard.html", broadcaster_id=request.args.get("broadcaster_id", "1"))

@app.get("/dashboard")
def dashboard():
    return render_template("dashboard.html", broadcaster_id=request.args.get("broadcaster_id", "1"))

@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "SN7 Core API", "version": "1.3.0"})

@app.get("/api")
def api():
    return jsonify({"ok": True, "service": "SN7 Core API", "multi_streamer": True, "v_d": False, "modules": ["economy", "ranking", "duel", "commands", "settings", "kick"]})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
