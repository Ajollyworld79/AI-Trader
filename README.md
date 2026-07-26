# AI-Tader

Security-first tradingplatform i Python. Systemet kombinerer deterministisk
markedsanalyse og risikostyring med valgfri AI-klassifikation. AI kan aldrig
beregne position size, stop loss, take profit, eksponering eller godkende en
ordre.

> Projektet er software til analyse og automatisering, ikke finansiel
> rådgivning. LIVE-handel er slået fra som standard. Start og valider altid i
> paper/Saxo SIM.

## Hvad der er implementeret

- FastAPI REST API med PostgreSQL, SQLAlchemy og Alembic.
- Deterministiske EMA, SMA, RSI, ATR, MACD, ADX, Bollinger Bands,
  Volume Profile, relativ styrke, volatilitet, support og resistance.
- Risk engine med 0,5 % risiko pr. handel, højst 10 positioner, 20 %
  sektoreksponering, 2 % dagligt tab og 10 % drawdown.
- Long-only, ingen gearing, spread-, earnings- og datakvalitetskontrol.
- Automatisk Alpha Vantage-feed til nyheder/sentiment og kommende
  regnskabsdatoer. Data deduplikeres, begrænses og behandles som utroværdigt
  input.
- Saxo-baseret benchmarkanalyse, som klassificerer markedet deterministisk som
  bullish trend, bearish trend, sidelæns eller ekstrem volatilitet. Bearish og
  ekstremt volatile regimer afviser nye long-handler før AI.
- Paper broker og Saxo OpenAPI-adapter med pre-check, idempotency og
  broker-native stop/take-profit som relaterede ordrer.
- Ordreudførelse accepterer kun et frisk, gemt `buy`-signal. Kurser,
  kontoværdi, eksponering, stop, target og quantity hentes eller beregnes
  server-side og kan ikke angives af API-klienten.
- Positions-reconciliation med high-watermark, aktivering af broker-native
  trailing stop og sikker rækkefølge: stop ændres før fast take-profit fjernes.
- Krypteret lagring af Saxo access/refresh tokens, så OAuth-rotation overlever
  Railway-genstarter uden at lægge tokens i loggen eller databasen i klartekst.
  En database-lease forhindrer samtidige workers i at rotere samme refresh
  token på én gang.
- Eventdrevet backtest med commission, spread, slippage, dividends, splits,
  walk-forward-vinduer og en konservativ regel uden look-ahead.
- Databasehistorik for trades, signals, AI-kald, candles, features, orders,
  portfolio snapshots, performance, news, earnings, market regimes, backtests
  og audit logs.
- Separate workerroller til market, strategy, execution, position, news og
  scheduler.
- Docker og Railway-konfiguration til build, migration, healthcheck og drift.
- Lokale kvalitetskontroller med test, lint, typing og dependency-audit før
  ændringer pushes til Railway-kilden.

## AI-routing

Routingen er en kodeinvariant:

1. Alle GPT-5.4 Mini-kald sendes først til Azure OpenAI.
2. Hvis Azure-kaldet fejler, sendes samme validerede Mini-request til OpenAI.
3. Alle SOL-kald sendes direkte til OpenAI. SOL kan ikke routes til Azure.
4. SOL anvendes kun ved usikkerhed og højst til den konfigurerede 5 %-andel.
5. Hvert provider-forsøg logges separat med provider, model, tokens, latency,
   pris og resultat.

Begge providers skal være konfigureret, før AI aktiveres. Mangler én, kører
platformen videre uden AI og returnerer en fail-closed `hold`.

## Sikkert ordreflow

1. `POST /v1/analysis` gemmer analysen og returnerer et `signal_id`.
2. `POST /v1/orders` modtager kun `signal_id`, valgfri quantity-grænse og
   ordretype.
3. Serveren henter signalet, en frisk broker-quote og brokerens aktuelle
   portefølje.
4. Python beregner ATR-stop, profitmål, positionsstørrelse og alle limits.
5. Saxo pre-check køres, før entry med relateret stop/take-profit placeres.
6. En accepteret position registreres til løbende reconciliation.

Et signal kan kun forbruges én gang. Klientdata bruges dermed aldrig som
sandhed for pris, balance eller risiko.

## Hurtig lokal start

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
cp .env.example .env
.venv/bin/python -m app.security generate-key
```

Gem den rå nøgle i din password manager og indsæt kun dens `sha256` i
`API_KEY_HASHES`. Generér en separat admin-nøgle til `ADMIN_KEY_HASHES`.

```bash
alembic upgrade head
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Healthcheck:

```bash
curl http://127.0.0.1:8000/health
```

Autentificerede endpoints bruger:

```text
Authorization: Bearer <client-key>
X-Admin-Key: <admin-key>                 # kun administrative handlinger
Idempotency-Key: <unik 16-64 tegns nøgle> # ordreoprettelse
X-Live-Confirmation: <live-key>          # kun LIVE
```

## Sikker standard

- `TRADING_MODE=sim`
- `BROKER_BACKEND=paper`
- `TRADING_KILL_SWITCH=true`
- Ingen wildcard CORS eller hosts.
- Ingen API-dokumentation i production.
- Ingen hemmeligheder gemmes i Git eller logges.
- Ordre-POSTs retries aldrig automatisk; uklar brokerstatus markeres til
  reconciliation for at undgå dobbeltordrer.
- LIVE kræver Saxo, `LIVE_TRADING_ENABLED=true`, deaktiveret kill switch og
  en særskilt confirmation token. En frisk benchmarkanalyse og autoritativ
  earnings-status er også obligatorisk.

## Nyheder og markedsanalyse

News-workeren henter én global `NEWS_SENTIMENT`-feed og én samlet
`EARNINGS_CALENDAR` pr. refresh fra
[Alpha Vantage](https://www.alphavantage.co/documentation/). Standardintervallet
er seks timer, altså otte requests pr. døgn; den dokumenterede gratis kvote er
25 requests pr. døgn
([providerens supportside](https://www.alphavantage.co/support/)).

Market-workeren henter benchmark-candles gennem Saxos chart-endpoint og
beregner regime, volatilitet og trend i Python. Strategy-workeren samler
benchmarkets slutkurser, seneste friske regime, relevante nyheder og næste
regnskabsdato før Mini/SOL-vurderingen. Dashboardet viser status under
`market_intelligence`.

Se [SECURITY.md](SECURITY.md) og
[Railway-guiden](docs/RAILWAY_DEPLOYMENT.md). Gennemfør også
[LIVE-readiness-runbooken](docs/LIVE_READINESS.md), før du aktiverer eksterne
integrationer.

## Kvalitetsgates

```bash
.venv/bin/pytest
.venv/bin/ruff check app tests alembic
.venv/bin/black --check app tests alembic
.venv/bin/mypy app
.venv/bin/bandit -c pyproject.toml -r app
.venv/bin/pip-audit
```

Testpakken håndhæver mindst 80 % coverage.
