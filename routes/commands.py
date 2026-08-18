from flask import Blueprint, jsonify, request
from core.database import get_conn

commands_bp = Blueprint("commands", __name__)

@commands_bp.get("/<int:broadcaster_id>")
def list_commands(broadcaster_id):
    try:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT command, response FROM custom_commands
                       WHERE broadcaster_user_id=%s ORDER BY command""",
                    (broadcaster_id,)
                )
                rows = [{"command": r[0], "response": r[1]} for r in cur.fetchall()]
        finally:
            conn.close()
        return jsonify({"ok": True, "commands": rows, "demo": False})
    except RuntimeError as exc:
        if "DATABASE_URL" in str(exc):
            return jsonify({"ok": True, "commands": [], "demo": True})
        raise

@commands_bp.post("/<int:broadcaster_id>")
def save_command(broadcaster_id):
    data = request.get_json(silent=True) or {}
    command = str(data.get("command", "")).strip().lower()
    response = str(data.get("response", "")).strip()
    if not command.startswith("!") or not response:
        return jsonify({"ok": False, "error": "comando/resposta inválidos"}), 400

    try:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO custom_commands
                       (broadcaster_user_id, command, response)
                       VALUES (%s,%s,%s)
                       ON CONFLICT (broadcaster_user_id, command)
                       DO UPDATE SET response=EXCLUDED.response, updated_at=NOW()""",
                    (broadcaster_id, command, response)
                )
            conn.commit()
        finally:
            conn.close()
        return jsonify({"ok": True})
    except RuntimeError as exc:
        if "DATABASE_URL" in str(exc):
            return jsonify({
                "ok": False,
                "demo": True,
                "error": "Modo demonstração: conecte um banco PostgreSQL no Render para salvar comandos."
            }), 503
        raise

@commands_bp.delete("/<int:broadcaster_id>")
def delete_command(broadcaster_id):
    command = request.args.get("command", "").strip().lower()
    try:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """DELETE FROM custom_commands
                       WHERE broadcaster_user_id=%s AND command=%s""",
                    (broadcaster_id, command)
                )
                deleted = cur.rowcount > 0
            conn.commit()
        finally:
            conn.close()
        return jsonify({"ok": True, "deleted": deleted})
    except RuntimeError as exc:
        if "DATABASE_URL" in str(exc):
            return jsonify({
                "ok": False,
                "demo": True,
                "error": "Modo demonstração: conecte um banco PostgreSQL no Render para salvar comandos."
            }), 503
        raise
