from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any

from app.broker.base import BrokerValidationError
from app.domain.entities import BrokerOrder, BrokerOrderResult


class SimulatedBroker:
    def __init__(self, initial_cash: float = 100_000.0) -> None:
        self.cash = initial_cash
        self._orders: dict[str, dict[str, Any]] = {}
        self._positions: dict[str, dict[str, Any]] = {}
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
                "protected_at_broker": True,
            }
            return BrokerOrderResult(
                broker_order_id=broker_order_id,
                related_order_ids=related,
                status="filled",
                raw=record,
            )
