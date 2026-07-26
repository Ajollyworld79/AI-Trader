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
    BrokerCredentialModel,
    CandleModel,
    FeatureModel,
    ManagedPositionModel,
    NewsModel,
    OrderModel,
    PerformanceModel,
    PortfolioSnapshotModel,
    SignalModel,
    TradeModel,
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

    def get_signal(self, signal_id: str) -> dict[str, Any] | None:
        with self.database.session() as session:
            model = session.get(SignalModel, signal_id)
            if model is None:
                return None
            return {
                "id": model.id,
                "created_at": model.created_at,
                "symbol": model.symbol,
                "decision": model.decision,
                "confidence": model.confidence,
                "reason": model.reason,
                "risk_flags": list(model.risk_flags),
                "feature_snapshot": dict(model.feature_snapshot),
                "model_tier": model.model_tier,
            }

    def get_broker_credential(self, provider: str) -> dict[str, Any] | None:
        with self.database.session() as session:
            model = session.scalar(
                select(BrokerCredentialModel).where(BrokerCredentialModel.provider == provider)
            )
            if model is None:
                return None
            return {
                "encrypted_access_token": model.encrypted_access_token,
                "encrypted_refresh_token": model.encrypted_refresh_token,
                "access_expires_at": model.access_expires_at,
                "refresh_expires_at": model.refresh_expires_at,
            }

    def save_broker_credential(self, provider: str, **values: Any) -> None:
        allowed = {
            "encrypted_access_token",
            "encrypted_refresh_token",
            "access_expires_at",
            "refresh_expires_at",
        }
        if not values or not set(values).issubset(allowed):
            raise ValueError("invalid broker credential update")
        with self.database.session() as session:
            model = session.scalar(
                select(BrokerCredentialModel).where(BrokerCredentialModel.provider == provider)
            )
            if model is None:
                session.add(BrokerCredentialModel(provider=provider, **values))
            else:
                for key, value in values.items():
                    setattr(model, key, value)

    def claim_broker_refresh_lease(self, provider: str, owner: str, lease_until: datetime) -> bool:
        now = datetime.now(UTC)
        with self.database.session() as session:
            model = session.scalar(
                select(BrokerCredentialModel)
                .where(BrokerCredentialModel.provider == provider)
                .with_for_update()
            )
            if model is None:
                return False
            current_lease = model.refresh_lease_until
            if current_lease is not None and current_lease.tzinfo is None:
                current_lease = current_lease.replace(tzinfo=UTC)
            if current_lease is not None and current_lease > now:
                return model.refresh_lease_owner == owner
            model.refresh_lease_owner = owner
            model.refresh_lease_until = lease_until
            return True

    def release_broker_refresh_lease(self, provider: str, owner: str) -> None:
        with self.database.session() as session:
            model = session.scalar(
                select(BrokerCredentialModel)
                .where(BrokerCredentialModel.provider == provider)
                .with_for_update()
            )
            if model is not None and model.refresh_lease_owner == owner:
                model.refresh_lease_owner = None
                model.refresh_lease_until = None

    def save_candle(self, **values: Any) -> str:
        try:
            with self.database.session() as session:
                model = CandleModel(**values)
                session.add(model)
                session.flush()
                return model.id
        except IntegrityError:
            return ""

    def save_feature(self, **values: Any) -> str:
        with self.database.session() as session:
            model = FeatureModel(**values)
            session.add(model)
            session.flush()
            return model.id

    def save_news(self, **values: Any) -> str:
        with self.database.session() as session:
            model = NewsModel(**values)
            session.add(model)
            session.flush()
            return model.id

    def save_portfolio_snapshot(self, **values: Any) -> str:
        with self.database.session() as session:
            model = PortfolioSnapshotModel(**values)
            session.add(model)
            session.flush()
            return model.id

    def save_performance(self, **values: Any) -> str:
        with self.database.session() as session:
            model = PerformanceModel(**values)
            session.add(model)
            session.flush()
            return model.id

    def save_trade(self, **values: Any) -> str:
        with self.database.session() as session:
            model = TradeModel(**values)
            session.add(model)
            session.flush()
            return model.id

    def register_managed_position(self, **values: Any) -> str:
        with self.database.session() as session:
            model = ManagedPositionModel(**values)
            session.add(model)
            session.flush()
            return model.id

    def list_managed_positions(self) -> list[dict[str, Any]]:
        with self.database.session() as session:
            models = session.scalars(
                select(ManagedPositionModel)
                .where(
                    ManagedPositionModel.state.in_(
                        ("pending_entry", "open", "reconciliation_required")
                    )
                )
                .order_by(ManagedPositionModel.created_at)
            ).all()
            return [
                {
                    "id": model.id,
                    "symbol": model.symbol,
                    "sector": model.sector,
                    "uic": model.uic,
                    "asset_type": model.asset_type,
                    "quantity": model.quantity,
                    "entry_price": model.entry_price,
                    "atr": model.atr,
                    "stop_loss": model.stop_loss,
                    "take_profit": model.take_profit,
                    "high_watermark": model.high_watermark,
                    "broker_entry_order_id": model.broker_entry_order_id,
                    "broker_stop_order_id": model.broker_stop_order_id,
                    "broker_take_order_id": model.broker_take_order_id,
                    "trailing_active": model.trailing_active,
                    "state": model.state,
                    "last_error": model.last_error,
                }
                for model in models
            ]

    def update_managed_position(self, position_id: str, **values: Any) -> None:
        allowed = {
            "high_watermark",
            "stop_loss",
            "broker_take_order_id",
            "trailing_active",
            "state",
            "last_error",
        }
        if not values or not set(values).issubset(allowed):
            raise ValueError("invalid managed position update")
        with self.database.session() as session:
            model = session.get(ManagedPositionModel, position_id)
            if model is None:
                raise LookupError("managed position not found")
            for key, value in values.items():
                setattr(model, key, value)

    def update_trade_status(
        self,
        broker_order_id: str,
        *,
        status: str,
        exit_price: float | None = None,
        realized_pnl: float | None = None,
    ) -> None:
        with self.database.session() as session:
            model = session.scalar(
                select(TradeModel).where(TradeModel.broker_order_id == broker_order_id)
            )
            if model is None:
                return
            model.status = status
            if exit_price is not None:
                model.exit_price = exit_price
            if realized_pnl is not None:
                model.realized_pnl = realized_pnl

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

    def dashboard_summary(self) -> dict[str, float | int]:
        with self.database.session() as session:
            open_trades = session.scalar(
                select(func.count(TradeModel.id)).where(TradeModel.status == "open")
            )
            pending_orders = session.scalar(
                select(func.count(OrderModel.id)).where(
                    OrderModel.status.in_(("pending", "reconciliation_required"))
                )
            )
            realized_pnl = session.scalar(
                select(func.coalesce(func.sum(TradeModel.realized_pnl), 0.0))
            )
            latest_snapshot = session.scalar(
                select(PortfolioSnapshotModel)
                .order_by(PortfolioSnapshotModel.created_at.desc())
                .limit(1)
            )
            return {
                "open_trades": int(open_trades or 0),
                "pending_reconciliation": int(pending_orders or 0),
                "realized_pnl": float(realized_pnl or 0.0),
                "equity": float(latest_snapshot.equity) if latest_snapshot else 0.0,
                "cash": float(latest_snapshot.cash) if latest_snapshot else 0.0,
                "gross_exposure": (
                    float(latest_snapshot.gross_exposure) if latest_snapshot else 0.0
                ),
            }
