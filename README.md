# SN7 Core API

O SN7 Core concentra a economia, ranking, duelo, comandos personalizados e a integração oficial com a Kick.

## Regras

- Multi-streamer desde o início.
- Cada live possui sua própria economia.
- Nome, comando e emoji dos pontos são configuráveis.
- Ranking separado por live.
- Duelo usa pontos.
- V/D não faz parte do SN7 Core.
- Comandos personalizados são separados por live.
- PostgreSQL é o banco principal.
- Warzone, RedSec, Central API e Kick-Duelo antigo não fazem parte desta versão.

## Kick

O Core agora recebe `chat.message.sent` diretamente da Kick via webhook.

### Variáveis do Render

- `DATABASE_URL`
- `FLASK_SECRET_KEY`
- `KICK_CLIENT_ID`
- `KICK_CLIENT_SECRET`
- `KICK_REDIRECT_URI` = `https://SEU-DOMINIO/kick/callback`
- `KICK_WEBHOOK_URL` = `https://SEU-DOMINIO/kick/webhook`

Scopes usados:

`user:read chat:write events:subscribe`

### Conectar a Kick

Abra:

`https://SEU-DOMINIO/kick/login`

Depois da autorização, o Core salva o access token/refresh token no PostgreSQL e cria a assinatura de `chat.message.sent`.

### Endpoints

- `GET /kick/login`
- `GET /kick/callback`
- `GET /kick/status`
- `POST /kick/subscribe?broadcaster_id=ID`
- `POST /kick/webhook`

O webhook valida a assinatura da Kick e usa `Kick-Event-Message-Id` para impedir processamento duplicado.

## Render

Build:

`pip install -r requirements.txt`

Start:

`gunicorn app:app`
