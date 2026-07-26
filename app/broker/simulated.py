from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any, cast

from app.broker.base import BrokerValidationError
from app.domain.entities import (
    BrokerOrder,
    BrokerOrderResult,
    Candle,
    MarketSnapshot,
    PortfolioState,
)


class SimulatedBroker:
    def __init__(self, initial_cash: float = 100_000.0) -> None:
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.peak_equity = initial_cash
        self.daily_start_equity = initial_cash
        self._orders: dict[str, dict[str, Any]] = {}
        self._positions: dict[str, dict[str, Any]] = {}
        self._quotes: dict[str, tuple[float, float, datetime]] = {}
        self._candles: dict[int, list[Candle]] = {}
        self._lock = asyncio.Lock()

    async def health(self) -> bool:
        return True

    async def account_info(self) -> dict[str, object]:
        return {"mode": "sim", "account_key": "simulation", "currency": "DKK"}

    async def portfolio(self) -> dict[str, object]:
        return {
            "cash": self.cash,
            "positions": list(self._positions.values()),
            "orders": list(self._orders.values()),
        }

    async def positions(self) -> dict[str, object]:
        return {"data": list(self._positions.values())}

    async def orders(self) -> dict[str, object]:
        return {"data": list(self._orders.values())}

    async def balance(self) -> dict[str, object]:
        exposure = sum(
            float(position["entry_price"]) * int(position["quantity"])
            for position in self._positions.values()
        )
        return {
            "cash": self.cash,
            "market_value": exposure,
            "equity": self.cash + exposure,
        }

    def set_quote(self, symbol: str, *, bid: float, ask: float) -> None:
        if bid <= 0 or ask < bid:
            raise ValueError("invalid simulated quote")
        self._quotes[symbol.upper()] = (bid, ask, datetime.now(UTC))

    def set_candles(self, uic: int, candles: list[Candle]) -> None:
        self._candles[uic] = list(candles)

    async def historical_candles(
        self,
        *,
        uic: int,
        asset_type: str,
        horizon_minutes: int,
        count: int,
    ) -> list[Candle]:
        del asset_type, horizon_minutes
        return list(self._candles.get(uic, []))[-count:]

    async def market_snapshot(self, *, uic: int, asset_type: str, symbol: str) -> MarketSnapshot:
        del uic, asset_type
        bid, ask, as_of = self._quotes.get(symbol.upper(), (99.95, 100.05, datetime.now(UTC)))
        return MarketSnapshot(
            symbol=symbol.upper(),
            bid=bid,
            ask=ask,
            as_of=as_of,
            data_quality_ok=True,
        )

    async def portfolio_state(self) -> PortfolioState:
        balance = await self.balance()
        equity = float(cast(float, balance["equity"]))
        self.peak_equity = max(self.peak_equity, equity)
        sector_exposure: dict[str, float] = {}
        for position in self._positions.values():
            sector = str(position.get("sector", "Unknown"))
            value = float(position["entry_price"]) * int(position["quantity"])
            sector_exposure[sector] = sector_exposure.get(sector, 0.0) + value
        return PortfolioState(
            equity=equity,
            cash=self.cash,
            peak_equity=self.peak_equity,
            daily_pnl=equity - self.daily_start_equity,
            open_positions=len(self._positions),
            gross_exposure=float(cast(float, balance["market_value"])),
            sector_exposure=sector_exposure,
        )

    async def precheck(self, order: BrokerOrder) -> dict[str, object]:
        required = order.entry_price * order.quantity
        if required > self.cash:
            raise BrokerValidationError("insufficient simulation cash")
        if order.quantity <= 0:
            raise BrokerValidationError("quantity must be positive")
        if not order.stop_loss < order.entry_price < order.take_profit:
            raise BrokerValidationError("invalid bracket prices")
        return {"PreCheckResult": "Ok", "EstimatedCashRequired": required}

    async def place_bracket_order(self, order: BrokerOrder) -> BrokerOrderResult:
        async with self._lock:
            existing = self._orders.get(order.idempotency_key)
            if existing:
                return BrokerOrderResult(
                    broker_order_id=str(existing["broker_order_id"]),
                    related_order_ids=tuple(existing["related_order_ids"]),
                    status=str(existing["status"]),
                    raw=dict(existing),
                )
            await self.precheck(order)
            sequence = len(self._orders) + 1
            broker_order_id = f"SIM-{sequence:08d}"
            related = (f"{broker_order_id}-STOP", f"{broker_order_id}-TAKE")
            cost = order.entry_price * order.quantity
            self.cash -= cost
            record: dict[str, Any] = {
                **asdict(order),
                "broker_order_id": broker_order_id,
                "related_order_ids": list(related),
                "status": "filled",
            }
            self._orders[order.idempotency_key] = record
            self._positions[broker_order_id] = {
                "position_id": broker_order_id,
                "symbol": order.symbol,
                "quantity": order.quantity,
                "entry_price": order.entry_price,
                "stop_loss": order.stop_loss,
                "take_profit": order.take_profit,
                "related_order_ids": list(related),
                "protected_at_broker": True,
            }
            return BrokerOrderResult(
                broker_order_id=broker_order_id,
                related_order_ids=related,
                status="filled",
                raw=record,
            )

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
    ) -> dict[str, object]:
        del account_key, uic, asset_type
        if quantity <= 0 or order_price <= 0 or trailing_distance <= 0 or trailing_step <= 0:
            raise BrokerValidationError("invalid trailing stop request")
        for position in self._positions.values():
            related = position.get("related_order_ids", ())
            if order_id in related or order_id.endswith("-STOP"):
                position["stop_loss"] = order_price
                position["trailing_active"] = True
                position["trailing_distance"] = trailing_distance
                position["trailing_step"] = trailing_step
                return {"OrderId": order_id, "status": "replaced"}
        raise BrokerValidationError("simulated protective order not found")

    async def cancel_orders(
        self, *, account_key: str, order_ids: tuple[str, ...]
    ) -> dict[str, object]:
        del account_key
        if not order_ids:
            raise BrokerValidationError("at least one order id is required")
        for record in self._orders.values():
            related = list(record.get("related_order_ids", []))
            record["related_order_ids"] = [item for item in related if item not in order_ids]
        return {"Orders": [{"OrderId": item, "Status": "Cancelled"} for item in order_ids]}
