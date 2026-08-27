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
    "duel": ("!aposta", "Inicia uma aposta contra outro usuário.", "minigames", DEFAULT_APOSTA_RESPONSE),
    "slots": ("!slots", "Aposta pontos no cassino virtual da live.", "minigames", "🎰 $(user) apostou $(amount) $(currency): $(slots_result). Saldo: $(new_points) $(currency)."),
    "bet_accept": ("!aceitar", "Aceita uma aposta pendente.", "minigames", "$(bet_result)"),
    "bet_decline": ("!recusar", "Recusa uma aposta pendente.", "minigames", "$(bet_result)"),
    "cmds": ("!cmds", "Lista os comandos personalizados da live.", "public", "$(commands)"),
    "coinflip": ("!cara", "Joga cara ou coroa apostando pontos. Use !cara 20 ou !coroa 20.", "minigames", "🪙 $(user) jogou $(choice): $(coinflip_result). Saldo: $(new_points) $(currency)."),
    "coinflip_coroa": ("!coroa", "Joga coroa apostando pontos.", "minigames", "🪙 $(user) jogou coroa: $(coinflip_result). Saldo: $(new_points) $(currency)."),
    "poll": ("!enquete", "Cria uma enquete. Use pergunta | opção 1 | opção 2.", "minigames", "📊 $(poll_result)"),
    "vote": ("!votar", "Vota na enquete aberta.", "minigames", "📊 $(vote_result)"),
    "poll_close": ("!fecharenquete", "Fecha a enquete atual.", "admin", "📊 $(poll_result)"),
    "quiz": ("!quiz", "Inicia um quiz rápido.", "minigames", "🧠 $(quiz_result)"),
    "quiz_answer": ("!resposta", "Responde ao quiz atual.", "minigames", "🧠 $(quiz_result)"),
    "race": ("!corrida", "Entra na corrida da live.", "minigames", "🏃 $(race_result)"),
    "race_finish": ("!finalizacorrida", "Finaliza a corrida atual.", "admin", "🏁 $(race_result)"),
    "race_reset": ("!fimcrr", "Cancela e reseta a corrida atual sem distribuir prêmios.", "admin", "🛑 $(race_result)"),
    "target": ("!alvo", "Tenta acertar o número do alvo.", "minigames", "🎯 $(target_result)"),
    "secret": ("!numero", "Tenta descobrir o número secreto.", "minigames", "🔢 $(secret_result)"),
    "survival": ("!sobreviver", "Entra na rodada de sobrevivência.", "minigames", "🧟 $(survival_result)"),
    "survival_on": ("!sobrevivênciaon", "Streamer/mod inicia uma rodada de sobrevivência.", "admin", "🧟 $(survival_result)"),
    "survival_finish": ("!finalizarsobrevivencia", "Finaliza a rodada de sobrevivência.", "admin", "🧟 $(survival_result)"),
    "steal": ("!roubar", "Tenta roubar uma pequena parte dos pontos de outro usuário.", "minigames", "💰 $(steal_result)"),
    "vault": ("!cofre", "Tenta abrir o cofre.", "minigames", "🔐 $(vault_result)"),
    "jackpot": ("!jackpot", "Tenta ganhar parte do Jackpot da live.", "minigames", "👑 $(jackpot_result)"),
    "wzclass": ("!wzclass", "Consulta a classe Warzone usando os dados internos do Core.", "public", "$(wzclass)"),
    "addmusic": ("!addmusic", "Adiciona uma música à fila. Use nome do artista e da música ou um link de uma fonte permitida.", "music", "🎵 $(user) adicionou $(music) à fila. Posição: #$(queue_position)."),
    "skipmusic": ("!skip", "Pula a música que está tocando e passa para a próxima da fila.", "music", "⏭️ Música pulada. Próxima: $(music)."),
    "musicqueue": ("!queue", "Mostra a música atual e as próximas da fila.", "music", "🎵 $(queue)"),
    "nowplaying": ("!nowplaying", "Mostra a música que está tocando agora.", "music", "🎵 Tocando agora: $(music)."),
    "pausemusic": ("!pause", "Pausa a música atual do player.", "music", "⏸️ Música pausada."),
    "resumemusic": ("!resume", "Continua a música pausada.", "music", "▶️ Música retomada."),
    "clearmusic": ("!clearqueue", "Limpa a fila de músicas do canal.", "music", "🧹 Fila de músicas limpa."),
    "addcmd": ("!addcmd", "Cria ou atualiza um comando personalizado.", "admin", "✅ $(command) configurado."),
    "addpoint": ("!addpoint", "Adiciona pontos a um usuário ou a todos os participantes recentes do chat.", "admin", "🪙 $(target) recebeu +$(amount) $(currency). Saldo: $(new_points) $(currency)."),
    "settpoint": ("!setpoint", "Define o saldo de um usuário.", "admin", "🪙 Saldo de $(target): $(new_points) $(currency)."),
    "delcmd": ("!delcmd", "Remove um comando personalizado.", "admin", "🗑️ $(command) removido."),
}


MINIGAME_COMMAND_KEYS = {
    "bets": ("duel", "bet_accept", "bet_decline"),
    "slots": ("slots",),
    "coinflip": ("coinflip", "coinflip_coroa"),
    "polls": ("poll", "vote"),
    "quiz": ("quiz", "quiz_answer"),
    "race": ("race",),
    "target": ("target",),
    "secret": ("secret",),
    "survival": ("survival", "survival_on"),
    "steal": ("steal",),
    "vault": ("vault",),
    "jackpot": ("jackpot",),
}

def get_minigame_command_keys(game):
    return MINIGAME_COMMAND_KEYS.get(str(game or "").strip().lower(), ())

def set_minigame_commands_enabled(bid, game, enabled):
    keys = get_minigame_command_keys(game)
    if not keys:
        return 0
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE command_configs
                   SET enabled=%s, updated_at=NOW()
                 WHERE broadcaster_user_id=%s AND command_key = ANY(%s)
                """,
                (bool(enabled), int(bid), list(keys)),
            )
            changed = cur.rowcount
        conn.commit()
        forget_commands(bid)
        return changed
    finally:
        conn.close()

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

            # Sobrevivência aceita as grafias com e sem acento para evitar
            # comandos silenciosamente ignorados em chats mobile.
            survival_keys = {
                'survival_on': ('!sobrevivenciaon',),
                'survival': ('!sobrevivencia',),
            }
            for survival_key, aliases in survival_keys.items():
                cur.execute(
                    "SELECT id FROM command_configs WHERE broadcaster_user_id=%s AND command_key=%s",
                    (bid, survival_key),
                )
                row_cmd = cur.fetchone()
                if not row_cmd:
                    continue
                for alias in aliases:
                    cur.execute(
                        "SELECT 1 FROM command_aliases WHERE broadcaster_user_id=%s AND command_id=%s AND alias=%s",
                        (bid, row_cmd[0], alias),
                    )
                    if not cur.fetchone():
                        cur.execute(
                            "INSERT INTO command_aliases(broadcaster_user_id,command_id,alias) VALUES(%s,%s,%s)",
                            (bid, row_cmd[0], alias),
                        )

            # Atalho para destravar uma corrida presa sem distribuir prêmios.
            cur.execute(
                "SELECT id FROM command_configs WHERE broadcaster_user_id=%s AND command_key=%s",
                (bid, "race_reset"),
            )
            race_reset_row = cur.fetchone()
            if race_reset_row:
                cur.execute(
                    "SELECT 1 FROM command_aliases WHERE broadcaster_user_id=%s AND command_id=%s AND alias=%s",
                    (bid, race_reset_row[0], "!fimcorrida"),
                )
                if not cur.fetchone():
                    cur.execute(
                        "INSERT INTO command_aliases(broadcaster_user_id,command_id,alias) VALUES(%s,%s,%s)",
                        (bid, race_reset_row[0], "!fimcorrida"),
                    )

            # Organização do painel: categorias antigas são migradas sem alterar
            # a palavra de ativação nem a permissão real do comando.
            cur.execute(
                """
                UPDATE command_configs
                   SET category='admin', updated_at=NOW()
                 WHERE broadcaster_user_id=%s
                   AND command_key IN ('addcmd','addpoint','settpoint','delcmd')
                """,
                (bid,),
            )
            cur.execute(
                """
                UPDATE command_configs
                   SET category='music', updated_at=NOW()
                 WHERE broadcaster_user_id=%s
                   AND command_key IN ('addmusic','skipmusic','musicqueue','nowplaying','pausemusic','resumemusic','clearmusic')
                """,
                (bid,),
            )
            cur.execute(
                """
                UPDATE command_configs
                   SET category='minigames', updated_at=NOW()
                 WHERE broadcaster_user_id=%s
                   AND command_key IN ('duel','bet_accept','bet_decline','slots','coinflip','coinflip_coroa','poll','vote','quiz','quiz_answer','race','target','secret','survival','survival_on','steal','vault','jackpot')
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
                 ORDER BY CASE category WHEN 'public' THEN 1 WHEN 'music' THEN 2 WHEN 'minigames' THEN 3 WHEN 'admin' THEN 4 WHEN 'mod' THEN 4 ELSE 5 END, command
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


def get_minigame_command_status(bid):
    bid = int(bid)
    ensure_command_defaults(bid)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT command_key, enabled
                  FROM command_configs
                 WHERE broadcaster_user_id=%s
                   AND command_key = ANY(%s)
                """,
                (bid, list({key for keys in MINIGAME_COMMAND_KEYS.values() for key in keys})),
            )
            rows = {str(row[0]): bool(row[1]) for row in cur.fetchall()}
    finally:
        conn.close()

    # O card só fica ativo quando TODOS os comandos públicos daquele jogo
    # estão ativos. Assim, desligar um único comando também desliga o card.
    return {
        game: all(rows.get(key, False) for key in keys)
        for game, keys in MINIGAME_COMMAND_KEYS.items()
    }

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


def _sync_minigame_from_command(cur, bid, changed_key):
    game_keys = {
        "bets": ("duel", "bet_accept", "bet_decline"),
        "slots": ("slots",),
        "coinflip": ("coinflip", "coinflip_coroa"),
        "polls": ("poll", "vote"),
        "quiz": ("quiz", "quiz_answer"),
        "race": ("race",),
        "target": ("target",),
        "secret": ("secret",),
        "survival": ("survival", "survival_on"),
        "steal": ("steal",),
        "vault": ("vault",),
        "jackpot": ("jackpot",),
    }
    game = next((name for name, keys in game_keys.items() if changed_key in keys), None)
    if not game:
        return
    keys = game_keys[game]
    cur.execute(
        "SELECT command_key,enabled FROM command_configs WHERE broadcaster_user_id=%s AND command_key = ANY(%s)",
        (bid, list(keys)),
    )
    states = {str(row[0]): bool(row[1]) for row in cur.fetchall()}
    active = all(states.get(key, False) for key in keys)
    field = f"{game}_enabled"
    # Import local para evitar ciclo no carregamento do módulo.
    from core.minigames import SUPPORTED_PLATFORMS
    for platform in SUPPORTED_PLATFORMS:
        cur.execute(
            f"UPDATE minigame_settings SET {field}=%s,updated_at=NOW() WHERE broadcaster_user_id=%s AND platform=%s",
            (active, bid, platform),
        )


def update_command(bid, key, command=None, response=None, enabled=None, description=None, reset_aliases=False, aliases=None):
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

            normalized_aliases = None
            if aliases is not None:
                normalized_aliases = []
                seen_aliases = set()
                for raw_alias in aliases:
                    alias = str(raw_alias or "").strip().lower()
                    if not alias:
                        continue
                    if not alias.startswith("!"):
                        raise ValueError("A variante deve começar com !")
                    if len(alias) > 64:
                        raise ValueError("A variante é muito longa.")
                    if alias == str(command or "").strip().lower():
                        raise ValueError("A variante não pode ser igual ao comando principal.")
                    if alias not in seen_aliases:
                        seen_aliases.add(alias)
                        normalized_aliases.append(alias)

                cur.execute(
                    """
                    SELECT command, id
                      FROM command_configs
                     WHERE broadcaster_user_id=%s AND command_key=%s
                    """,
                    (int(bid), key),
                )
                target_row = cur.fetchone()
                if not target_row:
                    raise ValueError("Comando não encontrado.")

                for alias in normalized_aliases:
                    cur.execute(
                        """
                        SELECT 1 FROM command_configs
                         WHERE broadcaster_user_id=%s AND command=%s AND command_key<>%s
                        UNION ALL
                        SELECT 1 FROM command_aliases ca
                         JOIN command_configs cc ON cc.id=ca.command_id
                         WHERE ca.broadcaster_user_id=%s AND ca.alias=%s AND cc.command_key<>%s
                        LIMIT 1
                        """,
                        (int(bid), alias, key, int(bid), alias, key),
                    )
                    if cur.fetchone():
                        raise ValueError(f"A palavra de ativação {alias} já está em uso.")

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

            if normalized_aliases is not None:
                cur.execute(
                    """
                    SELECT id FROM command_configs
                     WHERE broadcaster_user_id=%s AND command_key=%s
                    """,
                    (int(bid), key),
                )
                command_row = cur.fetchone()
                if command_row:
                    cur.execute(
                        "DELETE FROM command_aliases WHERE broadcaster_user_id=%s AND command_id=%s",
                        (int(bid), command_row[0]),
                    )
                    for alias in normalized_aliases:
                        cur.execute(
                            """
                            INSERT INTO command_aliases(broadcaster_user_id,command_id,alias)
                            VALUES(%s,%s,%s)
                            """,
                            (int(bid), command_row[0], alias),
                        )

            if command is not None and key == "points":
                cur.execute(
                    "UPDATE channels SET currency_command=%s,updated_at=NOW() WHERE broadcaster_user_id=%s",
                    (command, int(bid)),
                )

            # Mini Games e comandos precisam permanecer sincronizados nos dois sentidos.
            # Os comandos do Core são globais por canal; portanto a configuração do
            # Mini Game é refletida em todas as plataformas conectadas.
            if enabled is not None:
                _sync_minigame_from_command(cur, int(bid), key)

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
