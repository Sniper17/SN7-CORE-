# SN7 Core 1.9.56 — Loja Stable

## Loja
- Corrigido o comando público `!loja` para listar os itens ativos com estoque disponível e sempre enviar o link da loja do canal correto.
- Mantido o acesso público por `/loja/<canal>` sem substituir a sessão administrativa do streamer.
- Mantida a carteira de pontos separada por `broadcaster_user_id`.
- Mantida a proteção para que o login de viewer na Loja não entre no painel do streamer.
- Mantida a criação de itens com nome, imagem, preço, estoque e descrição.
- Adicionado suporte a recompensas do tipo áudio.
- Adicionado player de áudio com fila por canal para uso em Browser Source/OBS.
- Player usa token assinado específico do canal e não concede acesso administrativo.

## Estabilidade
- Corrigida a condição de corrida no cadastro de players que podia gerar `duplicate key` durante recompensas de presença.
- Corrigido o worker do YouTube: restaurados os locks por canal que estavam sendo referenciados sem definição.
- Nenhum reset ou alteração dos saldos existentes foi incluído nesta versão.
