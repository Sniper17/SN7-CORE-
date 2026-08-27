# SN7 Core 1.9.55 — Loja de comunidade

- Loja pública por canal, com URL própria.
- Login separado do viewer via Kick, sem substituir a sessão do streamer.
- Saldo do viewer consultado por canal usando a carteira atual de pontos.
- Recompensas com nome, descrição, preço, imagem e estoque opcional.
- Recompensas de áudio com URL ou upload pequeno e fila de resgate.
- Resgates transacionais: pontos são debitados somente dentro de uma transação e estoque é reduzido atomicamente.
- Histórico de resgates para o streamer.
- Proteção para não apagar itens que já possuem histórico.
- Nenhuma alteração ou reset nas configurações, comandos ou saldos existentes.

## Comando público `!loja`

- Adicionado `!loja` aos comandos públicos do canal.
- O comando publica diretamente o link da Loja daquele streamer.
- O comando não cria jogador, não altera pontos e não mistura carteiras.
- O login da Loja continua separado do login administrativo do painel, usando o fluxo dedicado de viewer (`/kick/store-login`).
- O retorno do OAuth mantém a sessão do streamer intacta e grava a conta do viewer em uma sessão específica da Loja.
