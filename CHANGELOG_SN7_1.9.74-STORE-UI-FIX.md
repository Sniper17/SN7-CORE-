# SN7 Core 1.9.74 — Store UI Fix

## Correções
- Painel administrativo da Loja não exibe mais skeletons/quadrados quando a categoria está vazia.
- Itens da Loja e histórico de resgates agora são carregados de forma independente; uma falha HTTP no histórico não transforma a área de itens em “Falha HTTP 500”.
- Exclusão de item/áudio não dispara submit, navegação ou recarga da página.
- Após excluir, o card é removido diretamente da interface.
- Ao excluir o último item ou áudio, o estado vazio correspondente aparece imediatamente.
- Clique nas categorias administrativas não propaga ações para elementos externos.

## Compatibilidade
- Mantida a API e a preservação do histórico da 1.9.72.
- Mantida a correção de UI da 1.9.73.
- Nenhuma mudança no fluxo normal dos viewers.
