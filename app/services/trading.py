from __future__ import annotations

import hashlib
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any, cast

from app.broker.base import Broker
from app.config import Settings
from app.domain.entities import (
    BrokerOrder,
    BrokerOrderResult,
    Decision,
    OrderSide,
    OrderType,
    PortfolioState,
    TradeProposal,
)
from app.domain.risk import RiskEngine
from app.infrastructure.repositories import DuplicateOrderError, Repository
from app.security import secret_matches


class TradingDisabledError(RuntimeError):
    pass


class LiveConfirmationError(RuntimeError):
    pass


class TradingService:
    def __init__(
        self,
        settings: Settings,
        risk_engine: RiskEngine,
        broker: Broker,
        repository: Repository,
    ) -> None:
        self.settings = settings
        self.risk_engine = risk_engine
        self.broker = broker
        self.repository = repository

    async def execute_signal(
        self,
        *,
        signal_id: str,
        requested_quantity: int | None,
        order_type: OrderType,
        idempotency_key: str,
        actor_key: str,
        correlation_id: str,
        live_confirmation: str | None,
    ) -> BrokerOrderResult:
        if self.settings.trading_kill_switch:
            raise TradingDisabledError("trading kill switch is active")
        if self.settings.trading_mode == "live":
            self._verify_live_confirmation(live_confirmation)
        signal = self.repository.get_signal(signal_id)
        if signal is None:
            raise ValueError("signal not found")
        if signal["decision"] != Decision.BUY.value:
            raise ValueError("only a persisted buy signal can be executed")
        created_at = cast(datetime, signal["created_at"])
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        age_seconds = (datetime.now(UTC) - created_at).total_seconds()
        if age_seconds < 0 or age_seconds > self.settings.signal_max_age_minutes * 60:
            raise ValueError("signal is stale")
        feature_snapshot = cast(dict[str, Any], signal["feature_snapshot"])
        execution_raw = feature_snapshot.get("_execution", {})
        execution = cast(dict[str, Any], execution_raw) if isinstance(execution_raw, dict) else {}
        uic = execution.get("uic")
        if not isinstance(uic, int) or uic <= 0:
            raise ValueError("signal is missing an executable Saxo UIC")
        asset_type = str(execution.get("asset_type", "Stock"))
        sector = str(execution.get("sector", "Unknown"))
        days_to_earnings_raw = execution.get("days_to_earnings")
        days_to_earnings = (
            int(days_to_earnings_raw)
            if isinstance(days_to_earnings_raw, int) and not isinstance(days_to_earnings_raw, bool)
            else None
        )
        if self.settings.trading_mode == "live" and days_to_earnings is None:
            raise ValueError("LIVE execution requires authoritative earnings data")
        atr_raw = feature_snapshot.get("atr_14")
        if not isinstance(atr_raw, int | float) or isinstance(atr_raw, bool) or atr_raw <= 0:
            raise ValueError("signal is missing a valid ATR")
        atr_value = float(atr_raw)
        snapshot = await self.broker.market_snapshot(
            uic=uic,
            asset_type=asset_type,
            symbol=str(signal["symbol"]),
        )
        snapshot_age = (datetime.now(UTC) - snapshot.as_of).total_seconds()
        data_quality_ok = (
            snapshot.data_quality_ok
            and 0 <= snapshot_age <= self.settings.max_market_data_age_seconds
        )
        entry_price = snapshot.entry_price
        stop_loss = entry_price - atr_value * self.settings.atr_stop_multiple
        if stop_loss <= 0:
            raise ValueError("deterministic stop loss is not valid")
        risk_per_share = entry_price - stop_loss
        take_profit = entry_price + risk_per_share * self.settings.reward_risk_ratio
        proposal = TradeProposal(
            symbol=str(signal["symbol"]),
            sector=sector,
            side=OrderSide.BUY,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            spread_pct=snapshot.spread_pct,
            days_to_earnings=days_to_earnings,
            data_quality_ok=data_quality_ok,
            requested_quantity=requested_quantity,
            uic=uic,
            asset_type=asset_type,
            order_type=order_type,
        )
        portfolio = await self.broker.portfolio_state()
        self.repository.save_portfolio_snapshot(
            equity=portfolio.equity,
            cash=portfolio.cash,
            gross_exposure=portfolio.gross_exposure,
            sector_exposure=portfolio.sector_exposure,
            country_exposure={},
            currency_exposure={},
            positions=[],
        )
        return await self.execute(
            proposal=proposal,
            portfolio=portfolio,
            idempotency_key=idempotency_key,
            actor_key=actor_key,
            correlation_id=correlation_id,
            live_confirmation=live_confirmation,
            signal_id=signal_id,
        )

    async def execute(
        self,
        *,
        proposal: TradeProposal,
        portfolio: PortfolioState,
        idempotency_key: str,
        actor_key: str,
        correlation_id: str,
        live_confirmation: str | None,
        signal_id: str | None = None,
    ) -> BrokerOrderResult:
        if self.settings.trading_kill_switch:
            raise TradingDisabledError("trading kill switch is active")
        if self.settings.trading_mode == "live":
            self._verify_live_confirmation(live_confirmation)
        decision = self.risk_engine.evaluate(proposal, portfolio)
        if not decision.approved:
            self.repository.record_audit(
                event_type="order_rejected",
                actor_hash=hashlib.sha256(actor_key.encode()).hexdigest(),
                correlation_id=correlation_id,
                outcome="risk_rejected",
                details={"symbol": proposal.symbol, "reasons": list(decision.reasons)},
            )
            raise ValueError(f"risk rejected order: {', '.join(decision.reasons)}")
        if proposal.uic is None:
            raise ValueError("broker UIC is required for execution")
        if not self.settings.saxo_account_key and self.settings.broker_backend == "saxo":
            raise ValueError("Saxo account key is unavailable")
        account_key = (
            self.settings.saxo_account_key.get_secret_value()
            if self.settings.saxo_account_key
            else "simulation"
        )
        order = BrokerOrder(
            idempotency_key=idempotency_key,
            account_key=account_key,
            uic=proposal.uic,
            asset_type=proposal.asset_type,
            symbol=proposal.symbol,
            side=proposal.side,
            quantity=decision.quantity,
            order_type=proposal.order_type,
            entry_price=proposal.entry_price,
            stop_loss=decision.stop_loss,
            take_profit=decision.take_profit,
        )
        try:
            record_id = self.repository.create_pending_order(
                idempotency_key=idempotency_key,
                signal_id=signal_id,
                symbol=order.symbol,
                side=order.side.value,
                quantity=order.quantity,
                order_type=order.order_type.value,
                payload={
                    key: value.value if hasattr(value, "value") else value
                    for key, value in asdict(order).items()
                    if key != "account_key"
                },
                broker_response=None,
                broker_order_id=None,
                status="pending",
                mode=self.settings.trading_mode,
            )
        except DuplicateOrderError:
            raise

        try:
            result = await self.broker.place_bracket_order(order)
        except Exception:
            self.repository.complete_order(
                record_id,
                status="reconciliation_required",
                broker_order_id=None,
                broker_response={"message": "broker request failed; do not retry automatically"},
            )
            raise
        self.repository.complete_order(
            record_id,
            status=result.status,
            broker_order_id=result.broker_order_id,
            broker_response=result.raw,
        )
        if len(result.related_order_ids) < 2:
            self.repository.complete_order(
                record_id,
                status="reconciliation_required",
                broker_order_id=result.broker_order_id,
                broker_response=result.raw,
            )
            raise ValueError(
                "broker did not confirm both protective orders; reconciliation required"
            )
        self.repository.save_trade(
            symbol=proposal.symbol,
            side=proposal.side.value,
            quantity=decision.quantity,
            entry_price=proposal.entry_price,
            exit_price=None,
            stop_loss=decision.stop_loss,
            take_profit=decision.take_profit,
            realized_pnl=None,
            status="open" if result.status == "filled" else "pending",
            mode=self.settings.trading_mode,
            broker_order_id=result.broker_order_id,
        )
        self.repository.register_managed_position(
            symbol=proposal.symbol,
            sector=proposal.sector,
            uic=proposal.uic,
            asset_type=proposal.asset_type,
            quantity=decision.quantity,
            entry_price=proposal.entry_price,
            atr=(proposal.entry_price - decision.stop_loss) / self.settings.atr_stop_multiple,
            stop_loss=decision.stop_loss,
            take_profit=decision.take_profit,
            high_watermark=proposal.entry_price,
            broker_entry_order_id=result.broker_order_id,
            broker_stop_order_id=result.related_order_ids[0],
            broker_take_order_id=result.related_order_ids[1],
            trailing_active=False,
            state="open" if result.status == "filled" else "pending_entry",
            last_error=None,
        )
        self.repository.record_audit(
            event_type="order_submitted",
            actor_hash=hashlib.sha256(actor_key.encode()).hexdigest(),
            correlation_id=correlation_id,
            outcome=result.status,
            details={
                "symbol": proposal.symbol,
                "broker_order_id": result.broker_order_id,
                "mode": self.settings.trading_mode,
            },
        )
        return result

    def _verify_live_confirmation(self, supplied: str | None) -> None:
        expected = self.settings.live_confirmation_token_hash
        if not supplied or expected is None:
            raise LiveConfirmationError("live confirmation is required")
        if not secret_matches(supplied, (expected.get_secret_value(),)):
            raise LiveConfirmationError("live confirmation is invalid")
