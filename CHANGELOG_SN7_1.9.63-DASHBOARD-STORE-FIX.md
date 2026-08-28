# SN7 Core 1.9.63 — Correção da Loja no Dashboard

- Corrigida a identificação do `broadcaster_id` usada pelo módulo administrativo da Loja.
- O script da Loja agora usa a variável global `BROADCASTER_ID` real do dashboard, em vez de depender de `window.BROADCASTER_ID`, que não é criado por uma declaração `const` global.
- Isso elimina o envio incorreto de `/api/store/0/items`, que causava `HTTP 500` e `Acesso negado: este canal pertence a outro streamer.` ao criar recompensas ou áudios.
- Atualizada a versão/cache para `1.9.63-dashboard-store`.
