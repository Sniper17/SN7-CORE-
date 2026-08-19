from core.database import get_conn

DEFAULT_POINTS_RESPONSE = "$(user), você tem $(points) $(currency).$(emoji_text)$(rank_text)"

SYSTEM = {
    "points": ("!pontos", "Consulta seu saldo de pontos.", "public", DEFAULT_POINTS_RESPONSE),
    "ranking": ("!ranking", "Mostra o ranking do canal.", "public", "$(ranking)"),
    "duel": ("!aposta", "Inicia uma aposta contra outro usuário.", "public", "$(duel_result)"),
    "cmds": ("!cmds", "Lista os comandos personalizados da live.", "public", "$(commands)"),
    "addcmd": ("!addcmd", "Cria ou atualiza um comando personalizado.", "mod", "✅ $(command) configurado."),
    "addpoint": ("!addpoint", "Adiciona pontos a um usuário.", "mod", "🪙 $(target) recebeu +$(amount) $(currency). Saldo: $(new_points) $(currency)."),
    "settpoint": ("!setpoint", "Define o saldo de um usuário.", "mod", "🪙 Saldo de $(target): $(new_points) $(currency)."),
    "delcmd": ("!delcmd", "Remove um comando personalizado.", "mod", "🗑️ $(command) removido."),
}


def ensure_command_defaults(bid):
    bid = int(bid)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT currency_command, points_response FROM channels WHERE broadcaster_user_id=%s",
                (bid,),
            )
            row = cur.fetchone()
            points_command = str((row[0] if row else None) or "!pontos").strip().lower()
            points_response = str((row[1] if row else None) or DEFAULT_POINTS_RESPONSE)
            # Migra apenas a resposta padrão antiga; não sobrescreve personalizações.
            old_default = "$(user), você tem $(points) $(currency). $(emoji) Sua posição no ranking é #$(rank)."
            if points_response.strip() == old_default:
                points_response = DEFAULT_POINTS_RESPONSE
                cur.execute(
                    "UPDATE channels SET points_response=%s WHERE broadcaster_user_id=%s",
                    (DEFAULT_POINTS_RESPONSE, bid),
                )
            if not points_command.startswith("!"):
                points_command = "!placos"

            for key, (command, description, category, response) in SYSTEM.items():
                if key == "points":
                    command = points_command
                    response = points_response
                cur.execute(
                    """
                    INSERT INTO command_configs
                        (broadcaster_user_id,command_key,command,description,response,enabled,category,is_system)
                    VALUES (%s,%s,%s,%s,%s,TRUE,%s,TRUE)
                    ON CONFLICT (broadcaster_user_id,command_key) DO NOTHING
                    """,
                    (bid, key, command, description, response, category),
                )

            cur.execute(
                """
                UPDATE command_configs
                   SET command=%s, response=%s, updated_at=NOW()
                 WHERE broadcaster_user_id=%s
                   AND command_key='points'
                   AND (command<>%s OR response<>%s)
                """,
                (points_command, points_response, bid, points_command, points_response),
            )

            cur.execute(
                "SELECT command,response FROM custom_commands WHERE broadcaster_user_id=%s",
                (bid,),
            )
            legacy = cur.fetchall()

            for command, response in legacy:
                command = str(command or "").strip().lower()
                if not command:
                    continue
                if not command.startswith("!"):
                    command = "!" + command

                cur.execute(
                    """
                    INSERT INTO command_configs
                        (broadcaster_user_id,command_key,command,description,response,enabled,category,is_system)
                    SELECT %s,%s,%s,%s,%s,TRUE,'custom',FALSE
                     WHERE NOT EXISTS (
                         SELECT 1 FROM command_configs
                          WHERE broadcaster_user_id=%s
                            AND (command_key=%s OR command=%s)
                     )
                    """,
                    (
                        bid, "custom:" + command, command,
                        "Comando personalizado desta live.", str(response or ""),
                        bid, "custom:" + command, command
                    ),
                )

            if legacy:
                cur.execute(
                    "DELETE FROM custom_commands WHERE broadcaster_user_id=%s",
                    (bid,),
                )

        conn.commit()
    finally:
        conn.close()


def _dict_from_row(row):
    if not row:
        return None
    return {
        "id": row[0], "broadcaster_user_id": row[1], "command_key": row[2],
        "command": row[3], "description": row[4], "response": row[5],
        "enabled": bool(row[6]), "category": row[7], "is_system": bool(row[8]),
        "aliases": [],
    }


def list_commands(bid):
    ensure_command_defaults(bid)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id,broadcaster_user_id,command_key,command,description,response,enabled,category,is_system
                  FROM command_configs
                 WHERE broadcaster_user_id=%s
                 ORDER BY CASE category WHEN 'public' THEN 1 WHEN 'mod' THEN 2 ELSE 3 END, command
                """,
                (int(bid),),
            )
            result = []
            for row in cur.fetchall():
                item = _dict_from_row(row)
                cur.execute(
                    "SELECT alias FROM command_aliases WHERE broadcaster_user_id=%s AND command_id=%s ORDER BY alias",
                    (int(bid), row[0]),
                )
                item["aliases"] = [x[0] for x in cur.fetchall()]
                result.append(item)
            return result
    finally:
        conn.close()


def find_command(bid, typed):
    ensure_command_defaults(bid)
    typed = str(typed or "").strip().lower()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.id,c.broadcaster_user_id,c.command_key,c.command,c.description,
                       c.response,c.enabled,c.category,c.is_system
                  FROM command_configs c
                  LEFT JOIN command_aliases a
                    ON a.command_id=c.id AND a.broadcaster_user_id=c.broadcaster_user_id
                 WHERE c.broadcaster_user_id=%s
                   AND (LOWER(c.command)=%s OR LOWER(a.alias)=%s)
                 ORDER BY c.id
                 LIMIT 1
                """,
                (int(bid), typed, typed),
            )
            return _dict_from_row(cur.fetchone())
    finally:
        conn.close()


def update_command(bid, key, command=None, response=None, enabled=None, description=None):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if command is not None:
                command = str(command).strip().lower()
                if not command.startswith("!"):
                    raise ValueError("O comando deve começar com !")
                if len(command) > 64:
                    raise ValueError("O comando é muito longo.")
                cur.execute(
                    """
                    SELECT 1 FROM command_configs
                     WHERE broadcaster_user_id=%s AND command=%s AND command_key<>%s
                    UNION ALL
                    SELECT 1 FROM command_aliases
                     WHERE broadcaster_user_id=%s AND alias=%s
                    LIMIT 1
                    """,
                    (int(bid), command, key, int(bid), command),
                )
                if cur.fetchone():
                    raise ValueError("Essa palavra de ativação já está em uso.")

            if response is not None:
                response = str(response).strip()
                if not response:
                    raise ValueError("A resposta não pode ficar vazia.")
                if len(response) > 500:
                    raise ValueError("A resposta pode ter no máximo 500 caracteres.")

            if description is not None:
                description = str(description).strip()[:200]

            fields, values = [], []
            for name, value in (
                ("command", command), ("response", response),
                ("enabled", enabled), ("description", description)
            ):
                if value is not None:
                    fields.append(name + "=%s")
                    values.append(value)

            if not fields:
                return

            values.extend([int(bid), key])
            cur.execute(
                "UPDATE command_configs SET " + ",".join(fields) +
                ",updated_at=NOW() WHERE broadcaster_user_id=%s AND command_key=%s",
                values,
            )
            if cur.rowcount == 0:
                raise ValueError("Comando não encontrado.")

            if command is not None and key == "points":
                cur.execute(
                    "UPDATE channels SET currency_command=%s,updated_at=NOW() WHERE broadcaster_user_id=%s",
                    (command, int(bid)),
                )
        conn.commit()
    finally:
        conn.close()


def add_alias(bid, key, alias):
    alias = str(alias or "").strip().lower()
    if not alias.startswith("!"):
        raise ValueError("A palavra de ativação deve começar com !")
    if len(alias) > 64:
        raise ValueError("O alias é muito longo.")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id,command FROM command_configs WHERE broadcaster_user_id=%s AND command_key=%s",
                (int(bid), key),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError("Comando não encontrado.")
            if alias == str(row[1]).lower():
                raise ValueError("Essa já é a palavra principal.")
            cur.execute(
                """
                SELECT 1 FROM command_configs WHERE broadcaster_user_id=%s AND command=%s
                UNION ALL
                SELECT 1 FROM command_aliases WHERE broadcaster_user_id=%s AND alias=%s
                LIMIT 1
                """,
                (int(bid), alias, int(bid), alias),
            )
            if cur.fetchone():
                raise ValueError("Essa palavra de ativação já está em uso.")
            cur.execute(
                "INSERT INTO command_aliases(broadcaster_user_id,command_id,alias) VALUES(%s,%s,%s)",
                (int(bid), row[0], alias),
            )
        conn.commit()
    finally:
        conn.close()


def delete_alias(bid, alias):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM command_aliases WHERE broadcaster_user_id=%s AND alias=%s",
                (int(bid), str(alias or "").strip().lower()),
            )
            deleted = cur.rowcount > 0
        conn.commit()
        return deleted
    finally:
        conn.close()


def delete_custom(bid, key):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT command FROM command_configs WHERE broadcaster_user_id=%s AND command_key=%s AND is_system=FALSE",
                (int(bid), key),
            )
            row = cur.fetchone()

            cur.execute(
                "DELETE FROM command_configs WHERE broadcaster_user_id=%s AND command_key=%s AND is_system=FALSE",
                (int(bid), key),
            )
            deleted = cur.rowcount > 0

            if row:
                cur.execute(
                    "DELETE FROM custom_commands WHERE broadcaster_user_id=%s AND command=%s",
                    (int(bid), str(row[0]).strip().lower()),
                )

        conn.commit()
        return deleted
    finally:
        conn.close()
