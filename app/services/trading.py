from __future__ import annotations

import hashlib
from dataclasses import asdict

from app.broker.base import Broker
from app.config import Settings
from app.domain.entities import BrokerOrder, BrokerOrderResult, PortfolioState, TradeProposal
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

    async def execute(
        self,
        *,
        proposal: TradeProposal,
        portfolio: PortfolioState,
        idempotency_key: str,
        actor_key: str,
        correlation_id: str,
        live_confirmation: str | None,
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
