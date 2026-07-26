from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.infrastructure.database import Database
from app.infrastructure.models import (
    AIInteractionModel,
    AuditLogModel,
    BacktestModel,
    OrderModel,
    SignalModel,
)


class DuplicateOrderError(RuntimeError):
    pass


class Repository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def save_signal(
        self,
        *,
        symbol: str,
        decision: str,
        confidence: float,
        reason: str,
        risk_flags: list[str],
        feature_snapshot: dict[str, Any],
        model_tier: str,
    ) -> str:
        with self.database.session() as session:
            model = SignalModel(
                symbol=symbol,
                decision=decision,
                confidence=confidence,
                reason=reason,
                risk_flags=risk_flags,
                feature_snapshot=feature_snapshot,
                model_tier=model_tier,
            )
            session.add(model)
            session.flush()
            return model.id

    def save_ai_interaction(self, **values: Any) -> None:
        with self.database.session() as session:
            session.add(AIInteractionModel(**values))

    def create_pending_order(self, **values: Any) -> str:
        try:
            with self.database.session() as session:
                model = OrderModel(**values)
                session.add(model)
                session.flush()
                return model.id
        except IntegrityError as exc:
            raise DuplicateOrderError("idempotency key already exists") from exc

    def complete_order(
        self,
        order_id: str,
        *,
        status: str,
        broker_order_id: str | None,
        broker_response: dict[str, Any],
    ) -> None:
        with self.database.session() as session:
            model = session.get(OrderModel, order_id)
            if model is None:
                raise LookupError("order not found")
            model.status = status
            model.broker_order_id = broker_order_id
            model.broker_response = broker_response

    def record_audit(
        self,
        *,
        event_type: str,
        actor_hash: str,
        correlation_id: str,
        outcome: str,
        details: dict[str, Any],
    ) -> None:
        with self.database.session() as session:
            session.add(
                AuditLogModel(
                    event_type=event_type,
                    actor_hash=actor_hash,
                    correlation_id=correlation_id,
                    outcome=outcome,
                    details=details,
                )
            )

    def save_backtest(self, **values: Any) -> str:
        with self.database.session() as session:
            model = BacktestModel(**values)
            session.add(model)
            session.flush()
            return model.id

    def ai_usage(self) -> dict[str, float | int]:
        now = datetime.now(UTC)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        with self.database.session() as session:
            monthly_cost = session.scalar(
                select(func.coalesce(func.sum(AIInteractionModel.estimated_cost_dkk), 0.0)).where(
                    AIInteractionModel.created_at >= month_start
                )
            )
            daily_cost = session.scalar(
                select(func.coalesce(func.sum(AIInteractionModel.estimated_cost_dkk), 0.0)).where(
                    AIInteractionModel.created_at >= day_start
                )
            )
            mini_calls = session.scalar(
                select(func.count(AIInteractionModel.id)).where(
                    AIInteractionModel.created_at >= month_start,
                    AIInteractionModel.model_tier == "mini",
                )
            )
            sol_calls = session.scalar(
                select(func.count(AIInteractionModel.id)).where(
                    AIInteractionModel.created_at >= month_start,
                    AIInteractionModel.model_tier == "sol",
                )
            )
        return {
            "monthly_cost_dkk": float(monthly_cost or 0.0),
            "daily_cost_dkk": float(daily_cost or 0.0),
            "mini_calls": int(mini_calls or 0),
            "sol_calls": int(sol_calls or 0),
        }
