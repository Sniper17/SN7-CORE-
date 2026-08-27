# SN7 Core 1.9.57 — Loja Platform Stable

## Loja pública
- Link `/loja/<canal>` agora entrega somente a experiência pública da loja.
- O painel, navegação e demais comandos do SN7 não são renderizados no modo viewer.
- Layout responsivo para PC e mobile.
- Login e retorno permanecem dentro da loja do canal.

## Pontos por plataforma
- Loja pública reconhece Kick, YouTube e Twitch.
- Carteira é consultada por canal + plataforma + usuário.
- Resgates passam a registrar a plataforma e o identificador externo.
- Dados antigos de resgates Kick continuam compatíveis.

## Administração
- `/loja` continua sendo o modo administrativo para o streamer autenticado.
- Criação, ativação/desativação e histórico continuam protegidos pela sessão do canal.

## Áudio
- Player inválido/expirado não exibe mais JSON cru: mostra uma tela amigável aguardando nova autorização.
- Player usa token específico do canal.
- Ao iniciar um áudio da loja, o volume da música do SN7 é reduzido temporariamente quando há música tocando.
- Ao terminar, o volume original é restaurado somente se o volume não tiver sido alterado manualmente nesse intervalo.
- Falhas de reprodução não deixam a fila presa: o resgate é finalizado e a música pode voltar ao volume anterior.

## Segurança e compatibilidade
- Nenhum endpoint administrativo da loja foi tornado público.
- Migrações são aditivas e preservam registros existentes.
- Validação de sintaxe Python executada com sucesso.
