# SN7 Core API

Base nova do SN7 para concentrar Worker Kick, economia, ranking, duelo, comandos e painel.

## Regras

- Multi-streamer desde o início.
- Cada live possui sua própria economia.
- Nome, comando e emoji dos pontos são configuráveis.
- Ranking separado por live.
- Duelo usa pontos.
- **V/D não faz parte do SN7 Core.**
- Comandos personalizados são separados por live.
- PostgreSQL é o banco principal.

## Render

Build:
`pip install -r requirements.txt`

Start:
`gunicorn app:app`

Configure `DATABASE_URL` e `FLASK_SECRET_KEY`.

## Observação

Esta versão é a fundação do Core. A migração integral do OAuth/webhook e de todos os comportamentos do Worker atual será feita preservando o sistema que já funciona.
