# SN7 Core 1.9.66 — Overlay Save Fix

- O botão de salvar do Overlay Studio continua único na área de configuração; não há botão global duplicado.
- O botão **Tela cheia** permanece disponível no preview, em desktop e mobile.
- O salvar do Overlay agora usa o mesmo loading de operação do painel.
- Sucesso e erro do salvar agora usam o pop-up padronizado do painel, sem `alert()` nativo do navegador.
- A chamada do editor usa a rota limpa `/api/overlay/<broadcaster_id>/config`.
- Mantida uma rota compatível para a URL antiga.
- Tratamento de resposta inválida/HTML foi adicionado para evitar o erro `Unexpected token '<'`.
