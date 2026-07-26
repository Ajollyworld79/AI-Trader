# Railway deployment

## Services

Opret ét Railway-projekt med:

- `api` fra dette repository.
- PostgreSQL.
- Redis.
- Worker-services efter behov: `market`, `strategy`, `execution`, `position`,
  `news`, `scheduler`. De bruger samme image med start command
  `python -m app.workers.runner` og deres egen `APP_ROLE`.

Start med kun API, Postgres og Redis. Tilføj workers, når dataproviderne er
konfigureret, så tomme workers ikke bruger budget.

## API variables

Minimum i production:

```text
ENVIRONMENT=production
APP_ROLE=api
DATABASE_URL=${{Postgres.DATABASE_URL}}
REDIS_URL=${{Redis.REDIS_URL}}
ALLOWED_HOSTS=<railway-domain>
API_KEY_HASHES=<sha256>
ADMIN_KEY_HASHES=<sha256>
TRADING_MODE=sim
BROKER_BACKEND=paper
TRADING_KILL_SWITCH=true
```

AI aktiveres kun med hele sættet:

```text
AZURE_OPENAI_API_KEY
AZURE_OPENAI_ENDPOINT
AZURE_OPENAI_MINI_DEPLOYMENT=gpt-5.4-mini
OPENAI_API_KEY
OPENAI_MINI_MODEL=gpt-5.4-mini
OPENAI_SOL_MODEL=gpt-5.4
AI_AZURE_MINI_INPUT_DKK_PER_M
AI_AZURE_MINI_OUTPUT_DKK_PER_M
AI_OPENAI_MINI_INPUT_DKK_PER_M
AI_OPENAI_MINI_OUTPUT_DKK_PER_M
AI_OPENAI_SOL_INPUT_DKK_PER_M
AI_OPENAI_SOL_OUTPUT_DKK_PER_M
```

Priserne skal være de faktiske DKK-priser for dine deployments. Systemet
afviser delvis provider-konfiguration, så SOL aldrig ender på Azure og Mini har
en reel OpenAI fallback.

## Deploy

`railway.json` bygger Dockerfile, kører `alembic upgrade head` som pre-deploy,
starter API'et på Railway's `PORT` og kræver HTTP 200 fra `/health`, før
deployment skifter trafik.

Efter deploy:

1. Generér Railway-domain.
2. Sæt `ALLOWED_HOSTS` til hostnavnet uden `https://`.
3. Verificér `/health` og `/ready`.
4. Verificér at `/v1/system/status` giver 401 uden nøgle og 200 med nøgle.
5. Hold kill switch aktiv, indtil SIM-accepttesten er godkendt.

## Worker variables

Hver worker deler `DATABASE_URL`, `REDIS_URL` og sikkerhedspolitikken med API.
Sæt én af:

```text
APP_ROLE=market
APP_ROLE=strategy
APP_ROLE=execution
APP_ROLE=position
APP_ROLE=news
APP_ROLE=scheduler
```

Workers skal ikke have public domain. Giv kun execution/position-worker Saxo-
credentials; øvrige services skal følge least privilege.
