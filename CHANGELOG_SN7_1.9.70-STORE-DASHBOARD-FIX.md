# SN7 Core 1.9.70 — Correção real da Loja no Dashboard

- Corrige a tela Loja do painel principal, que ainda usava o layout antigo.
- Remove o botão Player da live da Loja.
- Separa Itens da Loja e Áudios da Loja em cards/categorias independentes.
- Dentro de cada categoria, mostra apenas os itens daquele tipo.
- Botões de ativação/desativação agora usam feedback visual com check padronizado.
- Botão Novo item/Novo áudio fica minimalista dentro da categoria.
- Descrição passa a ser realmente opcional no formulário do dashboard.
- Mantém imagem personalizada opcional, com fallback para a imagem do item e depois o padrão.
- Mantém o mesmo loading e toast/modal padrão do painel nas operações.
- Mantém o comando `!buy nome do item` já implementado no motor de chat.
- Mantém áudios na fila de áudio após a compra e eventos no Overlay.
