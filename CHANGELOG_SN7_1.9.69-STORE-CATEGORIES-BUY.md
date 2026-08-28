# SN7 Core 1.9.69 — Loja: categorias, compra via chat e feedback padronizado

## Correções
- Removido o botão `Player da live` da administração da Loja.
- Corrigido o fluxo de criação para não exigir descrição em itens simples.
- Corrigido o limite de imagem enviada pelo navegador para aceitar corretamente arquivos de até 2 MB após conversão para Data URL.
- Ações administrativas passam a usar o feedback visual padronizado do painel: loading, confirmação e aviso.

## Loja do streamer
- `Itens da Loja` e `Áudios da Loja` agora são cards/categorias independentes.
- Ao abrir uma categoria, somente os cards daquele tipo são exibidos.
- `Novo item` e `Novo áudio` abrem o mesmo formulário já direcionado ao tipo correto.
- A administração continua protegida pela sessão do streamer.

## Loja pública
- A página pública continua mostrando apenas os itens ativos e disponíveis para a comunidade.
- Itens e áudios permanecem separados em suas respectivas categorias.
- O resgate pela interface continua validando login, saldo e estoque.

## Compra pelo chat
- Novo comando de sistema `!buy`.
- Uso: `!buy nome do item`.
- O nome completo funciona e uma correspondência parcial única também é aceita.
- Se houver mais de um resultado, o bot pede o nome completo.
- A compra só é confirmada se o viewer tiver pontos suficientes.
- O débito de pontos e a baixa de estoque são transacionais e protegidos contra concorrência.
- Áudios comprados pelo chat entram automaticamente na fila de áudios da Loja.
- A compra também dispara o evento do overlay da SN7.
- Funciona no motor comum de chat para Kick, Twitch e YouTube.
