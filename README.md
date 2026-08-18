# SN7 Core API

Nova base do SN7: Worker + economia + ranking + duelo + comandos + painel em uma única aplicação.

## Regras da versão 1.0

- Multi-streamer desde o início.
- Cada live possui sua própria economia.
- Nome dos pontos configurável.
- Comando dos pontos configurável.
- Emoji configurável.
- Respostas configuráveis.
- Ranking separado por live.
- Duelo usa pontos, mas **não registra V/D**.
- Comandos personalizados separados por live.
- Painel web básico inspirado no conceito do StreamElements.
- PostgreSQL como banco principal.

## Rodar localmente

```bash
python -m venv .venv
# Linux/macOS:
source .venv/bin/activate
# Windows:
# .venv\Scripts\activate

pip install -r requirements.txt
cp .env.example .env
flask --app app run
```

## Render

Runtime: Python 3.12+
Build: `pip install -r requirements.txt`
Start: `gunicorn app:app`

Configure `DATABASE_URL` e `FLASK_SECRET_KEY`.

## Próximas etapas

1. Migrar 100% do comportamento do Worker atual.
2. Integrar OAuth real da Kick e subscriptions.
3. Migrar dados existentes do kick-duelo-api.
4. Adicionar configuração completa do Assalto ao Banco.
5. Completar o painel visual.
