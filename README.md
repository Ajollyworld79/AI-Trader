# AI-Trader

Security-first trading platform in Python. The system combines deterministic
market analysis and risk management with optional AI classification. The AI can
never calculate position size, stop loss, take profit or exposure, and it can
never approve an order.

> This project is software for analysis and automation, not financial
> advice. LIVE trading is turned off by default. Always start and validate in
> paper/Saxo SIM.

## What is implemented

- FastAPI REST API with PostgreSQL, SQLAlchemy and Alembic.
- Deterministic EMA, SMA, RSI, ATR, MACD, ADX, Bollinger Bands,
  Volume Profile, relative strength, volatility, support and resistance.
- Risk engine with 0.5% risk per trade, at most 10 positions, 20%
  sector exposure, 2% daily loss and 10% drawdown.
- Long-only, no leverage, and spread, earnings and data-quality checks.
- Automatic Alpha Vantage feed for news/sentiment and upcoming earnings
  dates. Data is deduplicated, rate-limited and treated as untrusted
  input.
- Saxo-based benchmark analysis that classifies the market deterministically as
  bullish trend, bearish trend, sideways or extreme volatility. Bearish and
  extremely volatile regimes reject new long trades before the AI is consulted.
- Paper broker and Saxo OpenAPI adapter with pre-check, idempotency and
  broker-native stop/take-profit as related orders.
- Order execution accepts only a fresh, stored `buy` signal. Prices,
  account value, exposure, stop, target and quantity are fetched or calculated
  server-side and cannot be supplied by the API client.
- Position reconciliation with high-watermark, activation of broker-native
  trailing stop and a safe sequence: the stop is changed before the fixed
  take-profit is removed.
- Encrypted storage of Saxo access/refresh tokens, so OAuth rotation survives
  Railway restarts without putting tokens in the log or in plaintext in the
  database. A database lease prevents concurrent workers from rotating the same
  refresh token at the same time.
- Event-driven backtest with commission, spread, slippage, dividends, splits,
  walk-forward windows and a conservative rule with no look-ahead.
- Database history for trades, signals, AI calls, candles, features, orders,
  portfolio snapshots, performance, news, earnings, market regimes, backtests
  and audit logs.
- Separate worker roles for market, strategy, execution, position, news and
  scheduler.
- Docker and Railway configuration for build, migration, healthcheck and
  operation.
- Local quality checks with tests, lint, typing and dependency audit before
  changes are pushed to the Railway source.

## AI routing

Routing is a code invariant:

1. All GPT-5.4 Mini calls are sent to Azure OpenAI first.
2. If the Azure call fails, the same validated Mini request is sent to OpenAI.
3. All SOL calls are sent directly to OpenAI. SOL cannot be routed to Azure.
4. SOL is used only under uncertainty and at most up to the configured 5% share.
5. Each provider attempt is logged separately with provider, model, tokens,
   latency, cost and result.

Both providers must be configured before AI is enabled. If one is missing, the
platform keeps running without AI and returns a fail-closed `hold`.

## Secure order flow

1. `POST /v1/analysis` stores the analysis and returns a `signal_id`.
2. `POST /v1/orders` accepts only `signal_id`, an optional quantity limit and
   the order type.
3. The server fetches the signal, a fresh broker quote and the broker's current
   portfolio.
4. Python calculates the ATR stop, profit target, position size and all limits.
5. The Saxo pre-check runs before the entry with a related stop/take-profit is
   placed.
6. An accepted position is registered for ongoing reconciliation.

A signal can be consumed only once. Client data is therefore never used as the
source of truth for price, balance or risk.

## Quick local start

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
cp .env.example .env
.venv/bin/python -m app.security generate-key
```

Store the raw key in your password manager and put only its `sha256` in
`API_KEY_HASHES`. Generate a separate admin key for `ADMIN_KEY_HASHES`.

```bash
alembic upgrade head
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Healthcheck:

```bash
curl http://127.0.0.1:8000/health
```

Authenticated endpoints use:

```text
Authorization: Bearer <client-key>
X-Admin-Key: <admin-key>                 # admin actions only
Idempotency-Key: <unique 16-64 character key> # order creation
X-Live-Confirmation: <live-key>          # LIVE only
```

## Safe defaults

- `TRADING_MODE=sim`
- `BROKER_BACKEND=paper`
- `TRADING_KILL_SWITCH=true`
- No wildcard CORS or hosts.
- No API documentation in production.
- No secrets are stored in Git or logged.
- Order POSTs are never retried automatically; an unclear broker status is
  flagged for reconciliation to avoid duplicate orders.
- LIVE requires Saxo, `LIVE_TRADING_ENABLED=true`, a disabled kill switch and
  a separate confirmation token. A fresh benchmark analysis and an authoritative
  earnings status are also mandatory.

## News and market analysis

The news worker fetches one global `NEWS_SENTIMENT` feed and one combined
`EARNINGS_CALENDAR` per refresh from
[Alpha Vantage](https://www.alphavantage.co/documentation/). The default
interval is six hours, i.e. eight requests per day; the documented free quota is
25 requests per day
([provider's support page](https://www.alphavantage.co/support/)).

The market worker fetches benchmark candles through Saxo's chart endpoint and
calculates regime, volatility and trend in Python. The strategy worker combines
the benchmark's closing prices, the latest fresh regime, relevant news and the
next earnings date before the Mini/SOL assessment. The dashboard shows status
under `market_intelligence`.

See [SECURITY.md](SECURITY.md) and the
[Railway guide](docs/RAILWAY_DEPLOYMENT.md). Also complete the
[LIVE readiness runbook](docs/LIVE_READINESS.md) before you enable external
integrations.

## Quality gates

```bash
.venv/bin/pytest
.venv/bin/ruff check app tests alembic
.venv/bin/black --check app tests alembic
.venv/bin/mypy app
.venv/bin/bandit -c pyproject.toml -r app
.venv/bin/pip-audit
```

The test suite enforces at least 80% coverage.
