# SN7 Core 1.9.58 — Loja Public Fix

## Correção principal
- Corrigido o template da `/loja/<canal>`: o modo viewer agora renderiza a experiência pública da Loja em vez de entregar um `<body>` vazio.
- O link público continua sem sidebar, topbar ou comandos do painel, em PC e mobile.
- O `/loja` autenticado continua no modo administrativo, sem alteração do fluxo de edição.

## Estabilidade
- Mantido o carregamento assíncrono de itens, saldo e login Kick/YouTube/Twitch.
- Reforçada a criação de carteiras de viewers para evitar erros de chave duplicada em mensagens simultâneas ou constraints legadas.

## Áudio
- Nenhuma alteração no fluxo do player de áudio nesta correção; o caminho de áudio existente permanece preservado.
