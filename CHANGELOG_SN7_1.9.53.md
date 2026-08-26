# SN7 Core 1.9.53 — correções de estabilidade

- Corrige persistência de estados dos Mini Games em PostgreSQL JSONB.
- Corrige o erro `cannot adapt type 'dict' using placeholder '%s'`.
- Adiciona normalização correta dos campos booleanos de Mini Games.
- Garante migração de `survival_duration_seconds` e demais colunas antes do uso.
- Move o bootstrap/migração do banco para o worker do webhook, evitando WORKER TIMEOUT.
- O webhook da Kick responde sem esperar a inicialização do PostgreSQL.
- Erros de status global dos Mini Games não silenciam mais o comando.
- Confirmação de redefinição do painel passa para uma camada modal absoluta acima de todos os editores.
- Unifica a versão/cache dos assets do dashboard em `1.9.53-minigames-stable`.
- Validação executada: Python `compileall` e JavaScript `node --check`.
