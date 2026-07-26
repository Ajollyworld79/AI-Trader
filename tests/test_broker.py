from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.broker.base import BrokerValidationError
from app.broker.saxo import SaxoBroker
from app.broker.simulated import SimulatedBroker
from app.config import Settings
from app.domain.entities import BrokerOrder, Candle, OrderSide, OrderType
from app.security import generate_key


def order(**overrides: object) -> BrokerOrder:
    values: dict[str, object] = {
        "idempotency_key": "unique-order-key-123456789",
        "account_key": "simulation",
        "uic": 1234,
        "asset_type": "Stock",
        "symbol": "MSFT",
        "side": OrderSide.BUY,
        "quantity": 10,
        "order_type": OrderType.MARKET,
        "entry_price": 100.0,
        "stop_loss": 95.0,
        "take_profit": 110.0,
    }
    values.update(overrides)
    return BrokerOrder(**values)  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_paper_broker_places_idempotent_protected_order() -> None:
    broker = SimulatedBroker()
    first = await broker.place_bracket_order(order())
    second = await broker.place_bracket_order(order())
    assert first.broker_order_id == second.broker_order_id
    assert len(first.related_order_ids) == 2
    portfolio = await broker.portfolio()
    position = portfolio["positions"][0]  # type: ignore[index]
    assert position["protected_at_broker"] is True


@pytest.mark.anyio
async def test_paper_broker_rejects_insufficient_cash() -> None:
    broker = SimulatedBroker(initial_cash=10)
    with pytest.raises(BrokerValidationError):
        await broker.precheck(order())


@pytest.mark.anyio
async def test_paper_broker_exposes_authoritative_state_and_manages_protection() -> None:
    broker = SimulatedBroker()
    candles = [
        Candle(
            timestamp=datetime.now(UTC) - timedelta(hours=index),
            open=100,
            high=101,
            low=99,
            close=100,
            volume=1_000,
        )
        for index in range(4, 0, -1)
    ]
    broker.set_candles(1234, candles)
    broker.set_quote("MSFT", bid=104.0, ask=104.2)

    assert (
        await broker.historical_candles(uic=1234, asset_type="Stock", horizon_minutes=60, count=2)
        == candles[-2:]
    )
    snapshot = await broker.market_snapshot(uic=1234, asset_type="Stock", symbol="MSFT")
    assert snapshot.entry_price == 104.2
    assert snapshot.spread_pct > 0

    placed = await broker.place_bracket_order(order())
    state = await broker.portfolio_state()
    assert state.open_positions == 1
    assert state.cash == 99_000
    changed = await broker.replace_protective_order(
        account_key="simulation",
        order_id=placed.related_order_ids[0],
        uic=1234,
        asset_type="Stock",
        quantity=10,
        order_price=101,
        trailing_distance=2,
        trailing_step=1,
    )
    assert changed["status"] == "replaced"
    cancelled = await broker.cancel_orders(
        account_key="simulation", order_ids=(placed.related_order_ids[1],)
    )
    assert cancelled["Orders"][0]["Status"] == "Cancelled"  # type: ignore[index]


def test_saxo_payload_contains_native_stop_and_take_profit() -> None:
    access_token = generate_key().raw
    settings = Settings(
        broker_backend="saxo",
        saxo_access_token=access_token,
        saxo_account_key="account",
    )
    broker = SaxoBroker(settings)
    payload = broker._order_payload(order())
    assert payload["Orders"][0]["OrderType"] == "Stop"  # type: ignore[index]
    assert payload["Orders"][1]["OrderType"] == "Limit"  # type: ignore[index]
    assert payload["ExternalReference"] == "unique-order-key-123456789"


def test_saxo_rejects_short_orders() -> None:
    access_token = generate_key().raw
    broker = SaxoBroker(Settings(broker_backend="saxo", saxo_access_token=access_token))
    with pytest.raises(BrokerValidationError):
        broker._order_payload(order(side=OrderSide.SELL))


@pytest.mark.anyio
async def test_saxo_bracket_order_prechecks_then_places_without_retry() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/precheck"):
            return httpx.Response(200, json={"PreCheckResult": "Ok"})
        return httpx.Response(
            200,
            json={
                "OrderId": "entry-1",
                "Orders": [{"OrderId": "stop-1"}, {"OrderId": "take-1"}],
            },
        )

    settings = Settings(
        broker_backend="saxo",
        saxo_access_token=generate_key().raw,
        saxo_account_key="account",
    )
    broker = SaxoBroker(settings)
    await broker.client.aclose()
    broker.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    broker.tokens.client = broker.client
    result = await broker.place_bracket_order(order(account_key="account"))
    assert result.broker_order_id == "entry-1"
    assert result.related_order_ids == ("stop-1", "take-1")
    assert len(requests) == 2
    assert all(request.headers["authorization"].startswith("Bearer ") for request in requests)
    await broker.close()


@pytest.mark.anyio
async def test_saxo_read_endpoints_and_error_mapping() -> None:
    def successful(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"path": request.url.path})

    settings = Settings(broker_backend="saxo", saxo_access_token=generate_key().raw)
    broker = SaxoBroker(settings)
    await broker.client.aclose()
    broker.client = httpx.AsyncClient(transport=httpx.MockTransport(successful))
    broker.tokens.client = broker.client
    assert await broker.health()
    assert "accounts" in str((await broker.account_info())["path"])
    combined = await broker.portfolio()
    assert set(combined) == {"positions", "orders", "balance"}
    await broker.close()

    def rejected(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"ErrorInfo": {"Message": "no"}})

    broker = SaxoBroker(settings)
    await broker.client.aclose()
    broker.client = httpx.AsyncClient(transport=httpx.MockTransport(rejected))
    broker.tokens.client = broker.client
    with pytest.raises(BrokerValidationError):
        await broker.account_info()
    await broker.close()


@pytest.mark.anyio
async def test_saxo_market_portfolio_chart_and_trailing_endpoints() -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        path = request.url.path
        if path.endswith("/infoprices"):
            return httpx.Response(
                200,
                json={"Quote": {"Bid": 99.5, "Ask": 100.0}, "DelayedByMinutes": 0},
            )
        if path.endswith("/charts"):
            return httpx.Response(
                200,
                json={
                    "Data": [
                        {
                            "Time": "2026-01-01T10:00:00Z",
                            "OpenBid": 99,
                            "OpenAsk": 101,
                            "HighBid": 100,
                            "HighAsk": 102,
                            "LowBid": 98,
                            "LowAsk": 100,
                            "CloseBid": 99.5,
                            "CloseAsk": 100.5,
                            "Volume": 1_000,
                        }
                    ]
                },
            )
        if path.endswith("/positions/me"):
            return httpx.Response(
                200,
                json={
                    "Data": [
                        {
                            "Sector": "Technology",
                            "PositionBase": {"Amount": 10},
                            "PositionView": {"CurrentPrice": 100},
                        }
                    ]
                },
            )
        if path.endswith("/balances/me"):
            return httpx.Response(
                200,
                json={
                    "TotalValue": 100_000,
                    "CashBalance": 80_000,
                    "HistoricalPeakEquity": 105_000,
                    "ProfitLossOnTradePositions": -100,
                },
            )
        return httpx.Response(200, json={"ok": True})

    settings = Settings(
        broker_backend="saxo",
        saxo_access_token=generate_key().raw,
        saxo_account_key="account",
    )
    broker = SaxoBroker(settings)
    await broker.client.aclose()
    broker.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    broker.tokens.client = broker.client

    quote = await broker.market_snapshot(uic=1234, asset_type="Stock", symbol="MSFT")
    candles = await broker.historical_candles(
        uic=1234, asset_type="Stock", horizon_minutes=60, count=20
    )
    state = await broker.portfolio_state()
    assert quote.ask == 100
    assert candles[0].close == 100
    assert state.gross_exposure == 1_000
    assert state.peak_equity == 105_000

    await broker.replace_protective_order(
        account_key="account",
        order_id="stop-1",
        uic=1234,
        asset_type="Stock",
        quantity=10,
        order_price=101,
        trailing_distance=2,
        trailing_step=0.5,
    )
    await broker.cancel_orders(account_key="account", order_ids=("take-1",))
    assert "PATCH" in methods
    assert "DELETE" in methods
    with pytest.raises(BrokerValidationError):
        await broker.cancel_orders(account_key="account", order_ids=("bad/id",))
    await broker.close()


def test_saxo_oauth_url_contains_state_and_never_secret() -> None:
    settings = Settings(
        broker_backend="saxo",
        saxo_app_key="app-key",
        saxo_app_secret=generate_key().raw,
        saxo_redirect_uri="https://example.com/callback",
    )
    broker = SaxoBroker(settings)
    url = broker.tokens.authorization_url("state-value-with-enough-entropy-123")
    assert "state=state-value-with-enough-entropy-123" in url
    assert "client_secret" not in url
