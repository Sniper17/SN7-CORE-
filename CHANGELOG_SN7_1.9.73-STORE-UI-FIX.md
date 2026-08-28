# SN7 Core 1.9.73 — Store UI Fix

## Correções
- Cards administrativos vazios da Loja agora mostram diretamente o estado vazio correto, sem skeletons/quadrados de carregamento.
- O carregamento da categoria de áudios não exibe placeholders visuais enquanto a lista está vazia.
- A exclusão de itens e áudios não provoca submit/navegação/reload da página.
- A exclusão é aplicada diretamente no card atual, sem recarregar toda a lista.
- Ao excluir o último item ou áudio, o estado vazio correspondente aparece imediatamente.
- Botões de ação dos cards agora usam `type="button"` e bloqueiam o comportamento padrão do clique.

## Compatibilidade
- Mantida a API e o comportamento de resgate.
- Mantida a preservação do histórico implementada na 1.9.72.
- As mudanças são restritas à experiência da Loja e não alteram o fluxo dos demais módulos.
