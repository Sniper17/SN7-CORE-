# SN7 CORE 1.9.59 — Loja pública: itens sincronizados

## Correção

- Corrigida a resolução do canal público da Loja.
- Slugs da Kick agora priorizam `sn7_profile_id`, o ID canônico usado pelo Core para unificar as plataformas.
- Evita que `/loja/<canal>` encontre uma linha antiga da tabela `channels` com o ID nativo da Kick e, por isso, consulte uma carteira/loja diferente.
- Mantida compatibilidade com IDs antigos e com canais sem `sn7_profile_id`.
- Itens criados no painel passam a aparecer corretamente na Loja pública do mesmo canal.
- O fluxo de resgate continua usando o ID canônico do canal, preservando a separação de pontos por streamer e plataforma.
