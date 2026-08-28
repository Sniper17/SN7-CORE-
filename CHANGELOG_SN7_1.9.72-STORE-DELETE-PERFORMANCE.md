# SN7 Core 1.9.72 — Store Delete & Performance

## Correções
- Itens da Loja agora podem ser excluídos mesmo depois de terem sido resgatados.
- O histórico de resgates é preservado após a exclusão.
- O nome do item é salvo no momento do resgate para manter o registro legível.
- A fila de áudios preserva nome, áudio e resgate mesmo quando o item original é excluído.

## Desempenho
- Cache interno curto para a listagem de itens da Loja, com invalidação automática após criar, editar, ativar/desativar ou excluir.
- Carregamento de itens e histórico do painel em paralelo.
- Cache privado curtíssimo para a resposta pública da Loja.
- Skeletons visuais durante o carregamento dos cards.
- Imagens dos itens carregadas com `lazy` e `decoding=async`.
- Cards fora da área visível usam `content-visibility` para reduzir trabalho de renderização.
- A página pública deixa o loader inicial rapidamente e exibe os skeletons enquanto os dados chegam.

## Compatibilidade
- O comando `!buy` continua funcionando normalmente.
- O comportamento de pontos, estoque, áudio e resgates existentes é preservado.
