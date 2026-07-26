from __future__ import annotations

from typing import Protocol

from app.domain.entities import (
    BrokerOrder,
    BrokerOrderResult,
    Candle,
    MarketSnapshot,
    PortfolioState,
)


class BrokerError(RuntimeError):
    pass


class BrokerAuthenticationError(BrokerError):
    pass


class BrokerValidationError(BrokerError):
    pass


class BrokerUnavailableError(BrokerError):
    pass


class Broker(Protocol):
    async def health(self) -> bool: ...

    async def account_info(self) -> dict[str, object]: ...

    async def portfolio(self) -> dict[str, object]: ...

    async def positions(self) -> dict[str, object]: ...

    async def orders(self) -> dict[str, object]: ...

    async def balance(self) -> dict[str, object]: ...

    async def portfolio_state(self) -> PortfolioState: ...

    async def market_snapshot(
        self, *, uic: int, asset_type: str, symbol: str
    ) -> MarketSnapshot: ...

    async def historical_candles(
        self,
        *,
        uic: int,
        asset_type: str,
        horizon_minutes: int,
        count: int,
    ) -> list[Candle]: ...

    async def precheck(self, order: BrokerOrder) -> dict[str, object]: ...

    async def place_bracket_order(self, order: BrokerOrder) -> BrokerOrderResult: ...

    async def replace_protective_order(
        self,
        *,
        account_key: str,
        order_id: str,
        uic: int,
        asset_type: str,
        quantity: int,
        order_price: float,
        trailing_distance: float,
        trailing_step: float,
    ) -> dict[str, object]: ...

    async def cancel_orders(
        self, *, account_key: str, order_ids: tuple[str, ...]
    ) -> dict[str, object]: ...
