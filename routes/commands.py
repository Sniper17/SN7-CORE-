from flask import Blueprint, jsonify, request

from core.database import get_conn
from core.command_system import (
    add_alias,
    delete_alias,
    delete_custom,
    list_commands,
    update_command,
    get_system_command_default,
)

commands_bp = Blueprint("commands", __name__)


def _normalize_aliases(raw):
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        raise ValueError("Variantes inválidas.")

    result = []
    seen = set()
    for value in raw:
        alias = str(value or "").strip().lower()
        if not alias:
            continue
        if not alias.startswith("!"):
            raise ValueError("A variante deve começar com !")
        if len(alias) > 64:
            raise ValueError("A variante é muito longa.")
        if alias not in seen:
            seen.add(alias)
            result.append(alias)
    return result


@commands_bp.get("/<int:broadcaster_id>")
def get_commands(broadcaster_id):
    try:
        return jsonify({"ok": True, "commands": list_commands(broadcaster_id), "demo": False})
    except Exception as exc:
        print(f"[COMMANDS] GET erro: {exc}", flush=True)
        return jsonify({"ok": False, "error": str(exc)}), 500


@commands_bp.post("/<int:broadcaster_id>")
def create_command(broadcaster_id):
    data = request.get_json(silent=True) or {}
    command = str(data.get("command") or "").strip().lower()
    response = str(data.get("response") or "").strip()
    description = str(data.get("description") or "Comando personalizado desta live.").strip()

    if not command.startswith("!"):
        return jsonify({"ok": False, "error": "O comando deve começar com !"}), 400
    if not response:
        return jsonify({"ok": False, "error": "A resposta não pode ficar vazia."}), 400
    if len(command) > 64:
        return jsonify({"ok": False, "error": "O comando é muito longo."}), 400
    if len(response) > 500:
        return jsonify({"ok": False, "error": "A resposta pode ter no máximo 500 caracteres."}), 400

    try:
        aliases = _normalize_aliases(data.get("aliases"))
        if command in aliases:
            return jsonify({"ok": False, "error": "A variante não pode ser igual ao comando principal."}), 400

        conn = get_conn()
        try:
            with conn.cursor() as cur:
                # O comando principal e todas as variantes precisam estar livres.
                candidates = [command] + aliases
                for word in candidates:
                    cur.execute(
                        """
                        SELECT 1 FROM command_configs
                         WHERE broadcaster_user_id=%s AND command=%s
                        UNION ALL
                        SELECT 1 FROM command_aliases
                         WHERE broadcaster_user_id=%s AND alias=%s
                        LIMIT 1
                        """,
                        (int(broadcaster_id), word, int(broadcaster_id), word),
                    )
                    if cur.fetchone():
                        return jsonify({
                            "ok": False,
                            "error": f"A palavra de ativação {word} já está em uso."
                        }), 409

                cur.execute(
                    """
                    INSERT INTO command_configs
                        (broadcaster_user_id,command_key,command,description,response,enabled,category,is_system)
                    VALUES (%s,%s,%s,%s,%s,%s,'custom',FALSE)
                    RETURNING id
                    """,
                    (
                        int(broadcaster_id),
                        "custom:" + command,
                        command,
                        description[:200],
                        response,
                        bool(data.get("enabled", True)),
                    ),
                )
                command_id = cur.fetchone()[0]

                for alias in aliases:
                    cur.execute(
                        """
                        INSERT INTO command_aliases(broadcaster_user_id,command_id,alias)
                        VALUES(%s,%s,%s)
                        """,
                        (int(broadcaster_id), command_id, alias),
                    )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        print(f"[COMMANDS] POST erro: {exc}", flush=True)
        return jsonify({"ok": False, "error": "Não foi possível criar o comando. " + str(exc)}), 500

    return jsonify({"ok": True, "commands": list_commands(broadcaster_id)})


@commands_bp.patch("/<int:broadcaster_id>/<path:key>")
def edit_command(broadcaster_id, key):
    data = request.get_json(silent=True) or {}
    try:
        update_command(
            broadcaster_id,
            key,
            command=data.get("command"),
            response=data.get("response"),
            enabled=data.get("enabled"),
            description=data.get("description"),
            reset_aliases=bool(data.get("reset_aliases")),
        )
        return jsonify({"ok": True, "commands": list_commands(broadcaster_id)})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        print(f"[COMMANDS] PATCH erro: {exc}", flush=True)
        return jsonify({"ok": False, "error": str(exc)}), 500


@commands_bp.delete("/<int:broadcaster_id>/<path:key>")
def delete_command(broadcaster_id, key):
    try:
        # Personalizado: remove de verdade. Sistema: alterna ativo/desativado.
        if not delete_custom(broadcaster_id, key):
            commands = list_commands(broadcaster_id)
            target = next((item for item in commands if item["command_key"] == key), None)
            if not target:
                return jsonify({"ok": False, "error": "Comando não encontrado."}), 404
            update_command(broadcaster_id, key, enabled=not bool(target["enabled"]))
        return jsonify({"ok": True, "commands": list_commands(broadcaster_id)})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        print(f"[COMMANDS] DELETE erro: {exc}", flush=True)
        return jsonify({"ok": False, "error": str(exc)}), 500


@commands_bp.post("/<int:broadcaster_id>/<path:key>/reset")
def reset_command(broadcaster_id, key):
    try:
        # Apenas devolve o padrão. Nada é persistido até o usuário clicar em Salvar.
        return jsonify({"ok": True, "default": get_system_command_default(broadcaster_id, key)})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        print(f"[COMMANDS] RESET erro: {exc}", flush=True)
        return jsonify({"ok": False, "error": str(exc)}), 500


@commands_bp.post("/<int:broadcaster_id>/<path:key>/aliases")
def create_alias(broadcaster_id, key):
    try:
        alias = (request.get_json(silent=True) or {}).get("alias")
        add_alias(broadcaster_id, key, alias)
        return jsonify({"ok": True, "commands": list_commands(broadcaster_id)})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        print(f"[COMMANDS] ALIAS POST erro: {exc}", flush=True)
        return jsonify({"ok": False, "error": str(exc)}), 500


@commands_bp.delete("/<int:broadcaster_id>/<path:key>/aliases")
def remove_alias(broadcaster_id, key):
    try:
        alias = (request.get_json(silent=True) or {}).get("alias")
        deleted = delete_alias(broadcaster_id, alias)
        return jsonify({"ok": True, "deleted": deleted, "commands": list_commands(broadcaster_id)})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        print(f"[COMMANDS] ALIAS DELETE erro: {exc}", flush=True)
        return jsonify({"ok": False, "error": str(exc)}), 500
