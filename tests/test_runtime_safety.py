from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from app.broker.saxo import SaxoTokenManager
from app.broker.simulated import SimulatedBroker
from app.config import Settings
from app.domain.entities import BrokerOrder, OrderSide, OrderType
from app.infrastructure.database import Base, Database
from app.infrastructure.repositories import Repository
from app.security import generate_key
from app.services.positions import PositionManager
from tests.conftest import candle_series


def repository_for(settings: Settings) -> Repository:
    database = Database(settings)
    Base.metadata.create_all(database.engine)
    return Repository(database)


@pytest.mark.anyio
async def test_saxo_tokens_are_encrypted_and_survive_restart(
    settings: Settings,
) -> None:
    encryption_key = Fernet.generate_key().decode()
    configured = settings.model_copy(
        update={
            "broker_backend": "saxo",
            "saxo_app_key": SecretStr("app-key"),
            "saxo_app_secret": SecretStr("app-secret-with-sufficient-entropy"),
            "token_encryption_key": SecretStr(encryption_key),
        }
    )
    repository = repository_for(configured)
    issued_access = generate_key().raw
    issued_refresh = generate_key().raw

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "access_token": issued_access,
                "refresh_token": issued_refresh,
                "expires_in": 1_200,
                "refresh_token_expires_in": 3_600,
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    manager = SaxoTokenManager(configured, client, repository)
    await manager.exchange_code("authorization-code")
    stored = repository.get_broker_credential("saxo_sim")
    assert stored is not None
    assert stored["encrypted_access_token"] != issued_access
    assert stored["encrypted_refresh_token"] != issued_refresh

    restarted = SaxoTokenManager(configured, client, repository)
    assert await restarted.access_token() == issued_access
    lease_until = datetime.now(UTC) + timedelta(seconds=30)
    assert repository.claim_broker_refresh_lease("saxo_sim", "worker-a", lease_until)
    assert not repository.claim_broker_refresh_lease("saxo_sim", "worker-b", lease_until)
    repository.release_broker_refresh_lease("saxo_sim", "worker-a")
    assert repository.claim_broker_refresh_lease("saxo_sim", "worker-b", lease_until)
    repository.release_broker_refresh_lease("saxo_sim", "worker-b")
    await client.aclose()
    repository.database.engine.dispose()


@pytest.mark.anyio
async def test_position_reconciliation_activates_broker_trailing_stop(
    settings: Settings,
) -> None:
    configured = settings.model_copy(
        update={
            "trading_kill_switch": False,
            "trailing_activation_pct": 0.05,
        }
    )
    repository = repository_for(configured)
    broker = SimulatedBroker()
    placed = await broker.place_bracket_order(
        BrokerOrder(
            idempotency_key="managed-position-order-key",
            account_key="simulation",
            uic=1234,
            asset_type="Stock",
            symbol="MSFT",
            side=OrderSide.BUY,
            quantity=10,
            order_type=OrderType.MARKET,
            entry_price=100,
            stop_loss=95,
            take_profit=110,
        )
    )
    position_id = repository.register_managed_position(
        symbol="MSFT",
        sector="Technology",
        uic=1234,
        asset_type="Stock",
        quantity=10,
        entry_price=100,
        atr=2,
        stop_loss=95,
        take_profit=110,
        high_watermark=100,
        broker_entry_order_id=placed.broker_order_id,
        broker_stop_order_id=placed.related_order_ids[0],
        broker_take_order_id=placed.related_order_ids[1],
        trailing_active=False,
        state="pending_entry",
    )
    manager = PositionManager(configured, broker, repository)

    broker.set_quote("MSFT", bid=102, ask=102.1)
    opened = await manager.reconcile()
    assert opened["opened"] == 1
    assert opened["trailed"] == 0

    broker.set_quote("MSFT", bid=108, ask=108.1)
    trailed = await manager.reconcile()
    assert trailed["trailed"] == 1
    managed = {item["id"]: item for item in repository.list_managed_positions()}[position_id]
    assert managed["trailing_active"] is True
    assert managed["broker_take_order_id"] is None
    assert managed["stop_loss"] >= 100

    broker._positions.clear()
    await broker.cancel_orders(account_key="simulation", order_ids=(placed.related_order_ids[0],))
    closed = await manager.reconcile()
    assert closed["closed"] == 1
    assert repository.list_managed_positions() == []
    repository.database.engine.dispose()


@pytest.mark.anyio
async def test_missing_broker_entry_is_flagged_for_manual_reconciliation(
    settings: Settings,
) -> None:
    repository = repository_for(settings)
    position_id = repository.register_managed_position(
        symbol="MSFT",
        sector="Technology",
        uic=1234,
        asset_type="Stock",
        quantity=10,
        entry_price=100,
        atr=2,
        stop_loss=95,
        take_profit=110,
        high_watermark=100,
        broker_entry_order_id="missing-entry",
        broker_stop_order_id="missing-stop",
        broker_take_order_id="missing-take",
        trailing_active=False,
        state="pending_entry",
    )
    result = await PositionManager(settings, SimulatedBroker(), repository).reconcile()
    assert result["errors"] == 1
    managed = {item["id"]: item for item in repository.list_managed_positions()}[position_id]
    assert managed["state"] == "reconciliation_required"
    assert "absent" in managed["last_error"]
    repository.database.engine.dispose()


def test_repository_persists_market_and_performance_records(settings: Settings) -> None:
    repository = repository_for(settings)
    candle = candle_series(1)[0]
    candle_id = repository.save_candle(
        symbol="MSFT",
        timeframe="60m",
        timestamp=candle.timestamp,
        open=candle.open,
        high=candle.high,
        low=candle.low,
        close=candle.close,
        volume=candle.volume,
        dividend=0,
        split_ratio=1,
    )
    assert candle_id
    assert (
        repository.save_candle(
            symbol="MSFT",
            timeframe="60m",
            timestamp=candle.timestamp,
            open=candle.open,
            high=candle.high,
            low=candle.low,
            close=candle.close,
            volume=candle.volume,
            dividend=0,
            split_ratio=1,
        )
        == ""
    )
    assert repository.save_feature(
        symbol="MSFT",
        as_of=candle.timestamp,
        values={"atr_14": 2.0},
        input_hash="input-hash",
    )
    assert repository.save_news(
        external_id="news-1",
        symbol="MSFT",
        published_at=candle.timestamp,
        source="test",
        headline="A headline",
        sentiment="positive",
        payload={},
    )
    assert repository.save_performance(
        period="day",
        realized_pnl=10,
        unrealized_pnl=5,
        sharpe=1.0,
        sortino=1.2,
        drawdown=0.01,
        win_rate=0.5,
        average_winner=20,
        average_loser=-10,
        expectancy=1.0,
    )
    repository.database.engine.dispose()
