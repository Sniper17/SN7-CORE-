from core.database import get_conn
from core.cache import get_cached_commands, set_cached_commands, forget_commands, forget_channel
from threading import RLock

_initialized_channels = set()
_initialized_lock = RLock()

DEFAULT_POINTS_RESPONSE = "$(user), você tem $(points) $(currency).$(emoji_text)$(rank_text)"
DEFAULT_APOSTA_RESPONSE = "$(user) está apostando $(amount) $(currency) contra $(target). Digite $(accept_command) ou $(decline_command)."

SYSTEM = {
    "points": ("!pontos", "Consulta seu saldo de pontos.", "public", DEFAULT_POINTS_RESPONSE),
    "ranking": ("!ranking", "Mostra o ranking do canal.", "public", "$(ranking)"),
    "duel": ("!aposta", "Inicia uma aposta contra outro usuário.", "public", DEFAULT_APOSTA_RESPONSE),
    "bet_accept": ("!aceitar", "Aceita uma aposta pendente.", "public", "$(bet_result)"),
    "bet_decline": ("!recusar", "Recusa uma aposta pendente.", "public", "$(bet_result)"),
    "cmds": ("!cmds", "Lista os comandos personalizados da live.", "public", "$(commands)"),
    "wzclass": ("!wzclass", "Consulta a classe Warzone usando os dados internos do Core.", "public", "$(wzclass)"),
    "addmusic": ("!addmusic", "Adiciona uma música à fila. Use nome do artista e da música ou um link de uma fonte permitida.", "public", "🎵 $(user) adicionou $(music) à fila. Posição: #$(queue_position)."),
    "skipmusic": ("!skip", "Pula a música que está tocando e passa para a próxima da fila.", "public", "⏭️ Música pulada. Próxima: $(music)."),
    "musicqueue": ("!queue", "Mostra a música atual e as próximas da fila.", "public", "🎵 $(queue)"),
    "nowplaying": ("!nowplaying", "Mostra a música que está tocando agora.", "public", "🎵 Tocando agora: $(music)."),
    "pausemusic": ("!pause", "Pausa a música atual do player.", "mod", "⏸️ Música pausada."),
    "resumemusic": ("!resume", "Continua a música pausada.", "mod", "▶️ Música retomada."),
    "clearmusic": ("!clearqueue", "Limpa a fila de músicas do canal.", "mod", "🧹 Fila de músicas limpa."),
    "addcmd": ("!addcmd", "Cria ou atualiza um comando personalizado.", "mod", "✅ $(command) configurado."),
    "addpoint": ("!addpoint", "Adiciona pontos a um usuário.", "mod", "🪙 $(target) recebeu +$(amount) $(currency). Saldo: $(new_points) $(currency)."),
    "settpoint": ("!setpoint", "Define o saldo de um usuário.", "mod", "🪙 Saldo de $(target): $(new_points) $(currency)."),
    "delcmd": ("!delcmd", "Remove um comando personalizado.", "mod", "🗑️ $(command) removido."),
}


def get_system_command_default(bid, key):
    """Retorna o padrão original sem alterar o banco. O reset é aplicado somente ao salvar."""
    key = str(key or "").strip()
    if key not in SYSTEM:
        raise ValueError("Somente comandos do sistema podem ser redefinidos.")
    command, description, category, response = SYSTEM[key]
    return {
        "command_key": key,
        "command": command,
        "description": description,
        "category": category,
        "response": response,
        "enabled": False if key == "wzclass" else True,
        "is_system": True,
        "aliases": [],
    }


def ensure_command_defaults(bid):
    bid = int(bid)
    with _initialized_lock:
        if bid in _initialized_channels:
            return
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT currency_name, currency_command, points_response FROM channels WHERE broadcaster_user_id=%s",
                (bid,),
            )
            row = cur.fetchone()
            currency_name = str((row[0] if row else None) or "Pontos").strip()
            points_command = str((row[1] if row else None) or "!pontos").strip().lower()
            points_response = str((row[2] if row else None) or DEFAULT_POINTS_RESPONSE)

            # Migra somente os defaults antigos.
            if currency_name == "Points":
                currency_name = "Pontos"
                cur.execute(
                    "UPDATE channels SET currency_name=%s WHERE broadcaster_user_id=%s",
                    ("Pontos", bid),
                )

            # Migra apenas a resposta padrão antiga; não sobrescreve personalizações.
            old_default = "$(user), você tem $(points) $(currency). $(emoji) Sua posição no ranking é #$(rank)."
            if points_response.strip() == old_default:
                points_response = DEFAULT_POINTS_RESPONSE
                cur.execute(
                    "UPDATE channels SET points_response=%s WHERE broadcaster_user_id=%s",
                    (DEFAULT_POINTS_RESPONSE, bid),
                )
            if not points_command.startswith("!"):
                points_command = "!pontos"

            for key, (command, description, category, response) in SYSTEM.items():
                default_enabled = key != "wzclass"
                if key == "points":
                    command = points_command
                    response = points_response
                cur.execute(
                    """
                    INSERT INTO command_configs
                        (broadcaster_user_id,command_key,command,description,response,enabled,category,is_system)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,TRUE)
                    ON CONFLICT (broadcaster_user_id,command_key) DO NOTHING
                    """,
                    (bid, key, command, description, response, default_enabled, category),
                )

            cur.execute(
                """
                UPDATE command_configs
                   SET command='!aposta', updated_at=NOW()
                 WHERE broadcaster_user_id=%s
                   AND command_key='duel'
                   AND LOWER(command)='!duelo'
                """,
                (bid,),
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

            # Migra somente os defaults antigos da aposta, sem sobrescrever personalizações.
            old_bet_defaults = {"$(duel_result)", "$(user) está apostando $(amount) points contra $(target)."}
            cur.execute(
                """
                UPDATE command_configs
                   SET response=%s, updated_at=NOW()
                 WHERE broadcaster_user_id=%s
                   AND command_key='duel'
                   AND (response IS NULL OR BTRIM(response)='' OR response = ANY(%s))
                """,
                (DEFAULT_APOSTA_RESPONSE, bid, list(old_bet_defaults)),
            )

            # O comando antigo !correr continua aceito como variante, mas o principal
            # exibido no painel passa a ser !recusar.
            cur.execute(
                "SELECT id,command FROM command_configs WHERE broadcaster_user_id=%s AND command_key='bet_decline'",
                (bid,),
            )
            decline_row = cur.fetchone()
            if decline_row and str(decline_row[1]).strip().lower() == '!correr':
                cur.execute(
                    "UPDATE command_configs SET command='!recusar',updated_at=NOW() WHERE id=%s",
                    (decline_row[0],),
                )
                cur.execute(
                    "SELECT 1 FROM command_aliases WHERE broadcaster_user_id=%s AND command_id=%s AND alias='!correr'",
                    (bid, decline_row[0]),
                )
                if not cur.fetchone():
                    cur.execute(
                        "INSERT INTO command_aliases(broadcaster_user_id,command_id,alias) VALUES(%s,%s,'!correr')",
                        (bid, decline_row[0]),
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


    with _initialized_lock:
        _initialized_channels.add(bid)
    forget_commands(bid)


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
    bid = int(bid)
    ensure_command_defaults(bid)
    cached = get_cached_commands(bid)
    if cached is not None:
        return cached
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
            rows = cur.fetchall()
            result = [_dict_from_row(row) for row in rows]

            # Busca todas as variantes em uma única consulta.
            # Antes era feito SELECT de aliases para cada comando (N+1).
            if rows:
                command_ids = [row[0] for row in rows]
                cur.execute(
                    """
                    SELECT command_id,alias
                      FROM command_aliases
                     WHERE broadcaster_user_id=%s
                       AND command_id = ANY(%s)
                     ORDER BY command_id,alias
                    """,
                    (int(bid), command_ids),
                )
                aliases_by_command = {}
                for command_id, alias in cur.fetchall():
                    aliases_by_command.setdefault(command_id, []).append(alias)

                for item, row in zip(result, rows):
                    item["aliases"] = aliases_by_command.get(row[0], [])

            set_cached_commands(bid, result)
            return result
    finally:
        conn.close()


def find_command(bid, typed):
    bid = int(bid)
    ensure_command_defaults(bid)
    typed = str(typed or "").strip().lower()

    cached = get_cached_commands(bid)
    if cached is None:
        list_commands(bid)
        cached = get_cached_commands(bid) or []

    for item in cached:
        if str(item.get("command") or "").lower() == typed:
            return item
        if typed in [str(alias).lower() for alias in item.get("aliases") or []]:
            return item
    return None


def update_command(bid, key, command=None, response=None, enabled=None, description=None, reset_aliases=False):
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

            if reset_aliases:
                cur.execute(
                    """
                    DELETE FROM command_aliases
                     WHERE broadcaster_user_id=%s
                       AND command_id=(
                           SELECT id FROM command_configs
                            WHERE broadcaster_user_id=%s AND command_key=%s
                       )
                    """,
                    (int(bid), int(bid), key),
                )
        conn.commit()
        forget_commands(bid)
        forget_channel(bid)
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
        forget_commands(bid)
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
        forget_commands(bid)
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
        forget_commands(bid)
        return deleted
    finally:
        conn.close()
