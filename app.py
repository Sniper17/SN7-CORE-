from flask import Flask, jsonify, render_template, request, redirect, session
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
from routes.twitch import twitch_bp
from routes.youtube import youtube_bp
import os
import re

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change-me")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

SN7_VERSION = "1.9.11"
SN7_STATIC_CACHE = "public, max-age=31536000, immutable"

app.register_blueprint(economy_bp, url_prefix="/api/economy")
app.register_blueprint(ranking_bp, url_prefix="/api/ranking")
app.register_blueprint(duel_bp, url_prefix="/api/duel")
app.register_blueprint(commands_bp, url_prefix="/api/commands")
app.register_blueprint(settings_bp, url_prefix="/api/settings")
app.register_blueprint(kick_bp, url_prefix="/kick")
app.register_blueprint(music_bp, url_prefix="/api/music")
app.register_blueprint(obs_bp)
app.register_blueprint(twitch_bp, url_prefix="/twitch")
app.register_blueprint(youtube_bp, url_prefix="/youtube")


@app.after_request
def response_headers(response):
    # Assets recebem cache longo porque o dashboard usa versões na URL.
    if request.path.startswith("/static/"):
        response.headers.setdefault("Cache-Control", SN7_STATIC_CACHE)
    elif request.path in {"/", "/dashboard", "/perfil"}:
        # O HTML do painel nunca deve ficar preso no cache do navegador/proxy.
        # Os assets continuam com cache longo porque usam ?v=SN7_VERSION.
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
    response.headers.setdefault("X-SN7-Version", SN7_VERSION)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    return response


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
    # A primeira pintura do dashboard não depende do PostgreSQL.
    # As rotas que realmente precisam do banco continuam inicializando-o
    # no primeiro request de API. Isso evita que um cold start do banco
    # deixe a tela do Perfil presa antes mesmo de o HTML aparecer.
    if request.path in {"/", "/dashboard", "/perfil"} or request.path.startswith("/static/"):
        return None
    if os.environ.get("DATABASE_URL"):
        init_db()


def _dashboard_context():
    # A abertura do painel não consulta o PostgreSQL. O snapshot do perfil é
    # gravado na sessão no callback da Kick e o /kick/me valida/atualiza os
    # dados em segundo plano. Assim o banco nunca bloqueia a primeira pintura.
    current = get_session_broadcaster_id(validate=False)
    broadcaster_id = str(current) if current is not None else None
    profile = None
    if current is not None:
        raw = session.get("kick_profile")
        if isinstance(raw, dict) and int(raw.get("id") or 0) == int(current):
            profile = {
                "id": int(current),
                "username": str(raw.get("username") or "").strip(),
                "profile_picture_url": str(raw.get("profile_picture_url") or "").strip(),
            }
    return {
        "broadcaster_id": broadcaster_id,
        "public_mode": broadcaster_id is None,
        "kick_profile": profile,
    }

@app.get("/")
def home():
    # O painel é público. O login fica somente no perfil.
    return render_template("dashboard.html", **_dashboard_context())


@app.get("/dashboard")
def dashboard():
    return render_template("dashboard.html", **_dashboard_context())


@app.get("/perfil")
def profile():
    return redirect("/?profile=1")


@app.get("/api/platforms/status")
def platform_status():
    """Retorna o estado das três plataformas em uma única consulta rápida."""
    bid = get_session_broadcaster_id()
    if bid is None:
        return jsonify({"ok": True, "authenticated": False, "broadcaster_id": None,
                        "platforms": {"kick": {"connected": False, "active": False},
                                      "twitch": {"connected": False, "active": False},
                                      "youtube": {"connected": False, "active": False}}})
    try:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT username, profile_picture_url, bot_active
                      FROM kick_connections
                     WHERE broadcaster_user_id=%s
                """, (int(bid),))
                kick = cur.fetchone()

                cur.execute("""
                    SELECT provider, external_user_id, username, display_name,
                           avatar_url, bot_active
                      FROM chat_connections
                     WHERE broadcaster_user_id=%s
                       AND provider IN ('twitch','youtube')
                """, (int(bid),))
                rows = {str(r[0]): r for r in cur.fetchall()}
        finally:
            conn.close()

        tw = rows.get("twitch")
        yt = rows.get("youtube")
        return jsonify({
            "ok": True,
            "authenticated": True,
            "broadcaster_id": str(bid),
            "platforms": {
                "kick": {
                    "connected": bool(kick),
                    "active": bool(kick and kick[2]),
                    "user": ({"id": int(bid), "username": kick[0],
                              "profile_picture_url": kick[1] or ""} if kick else None),
                },
                "twitch": {
                    "connected": bool(tw),
                    "active": bool(tw and tw[5]),
                    "user": ({"id": tw[1], "username": tw[2],
                              "display_name": tw[3], "avatar_url": tw[4] or ""} if tw else None),
                },
                "youtube": {
                    "connected": bool(yt),
                    "active": bool(yt and yt[5]),
                    "user": ({"id": yt[1], "username": yt[2],
                              "display_name": yt[3], "avatar_url": yt[4] or ""} if yt else None),
                },
            },
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502


@app.get("/health")
def health():
    return jsonify({
        "ok": True,
        "service": "SN7 Core API",
        "version": SN7_VERSION,
        "database_configured": bool(os.environ.get("DATABASE_URL")),
        "kick_webhook": True,
        "v_d": False
    })


@app.get("/api")
def api():
    return jsonify({
        "ok": True,
        "service": "SN7 Core API",
        "version": SN7_VERSION,
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
            "kick",
            "twitch",
            "youtube"
        ]
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
