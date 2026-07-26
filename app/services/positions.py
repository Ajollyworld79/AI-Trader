from __future__ import annotations

from typing import Any

from app.broker.base import Broker, BrokerError
from app.config import Settings
from app.infrastructure.repositories import Repository


class PositionManager:
    """Reconciles broker protection and activates deterministic trailing stops.

    The existing stop is changed before the fixed take-profit is cancelled. This
    ordering keeps the position protected if the second broker request fails.
    """

    def __init__(self, settings: Settings, broker: Broker, repository: Repository) -> None:
        self.settings = settings
        self.broker = broker
        self.repository = repository

    async def reconcile(self) -> dict[str, int]:
        raw_positions = await self.broker.positions()
        raw_orders = await self.broker.orders()
        position_ids = self._collect_ids(raw_positions)
        order_ids = self._collect_ids(raw_orders)
        result = {"checked": 0, "opened": 0, "closed": 0, "trailed": 0, "errors": 0}
        for managed in self.repository.list_managed_positions():
            result["checked"] += 1
            try:
                position_present = self._has_position(raw_positions, managed)
                if managed["state"] == "pending_entry":
                    if position_present or str(managed["broker_entry_order_id"]) in position_ids:
                        self.repository.update_managed_position(
                            str(managed["id"]), state="open", last_error=None
                        )
                        self.repository.update_trade_status(
                            str(managed["broker_entry_order_id"]), status="open"
                        )
                        managed["state"] = "open"
                        result["opened"] += 1
                    elif str(managed["broker_entry_order_id"]) not in order_ids:
                        self.repository.update_managed_position(
                            str(managed["id"]),
                            state="reconciliation_required",
                            last_error="entry is absent from both broker orders and positions",
                        )
                        result["errors"] += 1
                        continue
                if managed["state"] not in {"open", "reconciliation_required"}:
                    continue
                stop_id = managed.get("broker_stop_order_id")
                take_id = managed.get("broker_take_order_id")
                protection_ids = {
                    str(item) for item in (stop_id, take_id) if isinstance(item, str) and item
                }
                if not position_present and protection_ids.isdisjoint(order_ids):
                    self.repository.update_managed_position(
                        str(managed["id"]), state="closed", last_error=None
                    )
                    self.repository.update_trade_status(
                        str(managed["broker_entry_order_id"]), status="closed"
                    )
                    result["closed"] += 1
                    continue
                if not isinstance(stop_id, str) or not stop_id:
                    self.repository.update_managed_position(
                        str(managed["id"]),
                        state="reconciliation_required",
                        last_error="broker stop order id is missing",
                    )
                    result["errors"] += 1
                    continue
                snapshot = await self.broker.market_snapshot(
                    uic=int(managed["uic"]),
                    asset_type=str(managed["asset_type"]),
                    symbol=str(managed["symbol"]),
                )
                if not snapshot.data_quality_ok:
                    raise ValueError("market data is delayed or invalid")
                changed = await self._update_trailing(managed, snapshot.bid)
                if changed:
                    result["trailed"] += 1
            except (BrokerError, LookupError, TypeError, ValueError) as exc:
                self.repository.update_managed_position(
                    str(managed["id"]),
                    state="reconciliation_required",
                    last_error=f"{type(exc).__name__}: {exc}"[:2_000],
                )
                result["errors"] += 1
        self.repository.record_audit(
            event_type="position_reconciliation",
            actor_hash="internal-position-manager",
            correlation_id="scheduled",
            outcome="completed" if result["errors"] == 0 else "attention_required",
            details=result,
        )
        return result

    async def _update_trailing(self, managed: dict[str, Any], market_price: float) -> bool:
        entry = float(managed["entry_price"])
        atr = float(managed["atr"])
        high = max(float(managed["high_watermark"]), market_price)
        position_id = str(managed["id"])
        if high != float(managed["high_watermark"]):
            self.repository.update_managed_position(position_id, high_watermark=high)
        activation = entry * (1.0 + self.settings.trailing_activation_pct)
        if not bool(managed["trailing_active"]) and market_price < activation:
            return False
        step = atr * self.settings.trailing_step_atr
        if bool(managed["trailing_active"]) and high - float(managed["high_watermark"]) < step:
            return False
        new_stop = max(
            entry,
            float(managed["stop_loss"]),
            market_price - atr * self.settings.trailing_distance_atr,
        )
        account_key = (
            self.settings.saxo_account_key.get_secret_value()
            if self.settings.saxo_account_key
            else "simulation"
        )
        await self.broker.replace_protective_order(
            account_key=account_key,
            order_id=str(managed["broker_stop_order_id"]),
            uic=int(managed["uic"]),
            asset_type=str(managed["asset_type"]),
            quantity=int(managed["quantity"]),
            order_price=new_stop,
            trailing_distance=atr * self.settings.trailing_distance_atr,
            trailing_step=step,
        )
        take_id = managed.get("broker_take_order_id")
        if not bool(managed["trailing_active"]) and isinstance(take_id, str) and take_id:
            await self.broker.cancel_orders(account_key=account_key, order_ids=(take_id,))
            take_id = None
        self.repository.update_managed_position(
            position_id,
            high_watermark=high,
            stop_loss=new_stop,
            broker_take_order_id=take_id,
            trailing_active=True,
            state="open",
            last_error=None,
        )
        return True

    @classmethod
    def _collect_ids(cls, payload: object) -> set[str]:
        ids: set[str] = set()
        if isinstance(payload, dict):
            for key, value in payload.items():
                if key.lower() in {"related_order_ids", "relatedorderids"} and isinstance(
                    value, list | tuple
                ):
                    ids.update(str(item) for item in value if isinstance(item, str | int))
                    continue
                if key.lower() in {
                    "orderid",
                    "order_id",
                    "positionid",
                    "position_id",
                    "broker_order_id",
                } and isinstance(value, str | int):
                    ids.add(str(value))
                else:
                    ids.update(cls._collect_ids(value))
        elif isinstance(payload, list | tuple):
            for value in payload:
                ids.update(cls._collect_ids(value))
        return ids

    @classmethod
    def _has_position(cls, payload: object, managed: dict[str, Any]) -> bool:
        if isinstance(payload, dict):
            normalized = {str(key).lower(): value for key, value in payload.items()}
            managed_uic = int(managed["uic"])
            managed_symbol = str(managed["symbol"]).upper()
            raw_uic = normalized.get("uic")
            raw_symbol = normalized.get("symbol")
            amount = normalized.get("amount", normalized.get("quantity", 1))
            amount_is_open = not isinstance(amount, int | float) or float(amount) != 0
            if amount_is_open and (
                (isinstance(raw_uic, int) and raw_uic == managed_uic)
                or (isinstance(raw_symbol, str) and raw_symbol.upper() == managed_symbol)
            ):
                return True
            return any(cls._has_position(value, managed) for value in payload.values())
        if isinstance(payload, list | tuple):
            return any(cls._has_position(value, managed) for value in payload)
        return False
