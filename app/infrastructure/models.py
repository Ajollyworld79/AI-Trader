from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class Timestamped:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )


class TradeModel(Timestamped, Base):
    __tablename__ = "trades"
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    exit_price: Mapped[float | None] = mapped_column(Float)
    stop_loss: Mapped[float] = mapped_column(Float, nullable=False)
    take_profit: Mapped[float] = mapped_column(Float, nullable=False)
    realized_pnl: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)
    broker_order_id: Mapped[str | None] = mapped_column(String(100), unique=True)


class SignalModel(Timestamped, Base):
    __tablename__ = "signals"
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    risk_flags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    feature_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    model_tier: Mapped[str] = mapped_column(String(16), nullable=False)


class AIInteractionModel(Timestamped, Base):
    __tablename__ = "ai_interactions"
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    model_deployment: Mapped[str] = mapped_column(String(100), nullable=False)
    model_tier: Mapped[str] = mapped_column(String(16), nullable=False)
    prompt: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    response: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_cost_dkk: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class BrokerCredentialModel(Timestamped, Base):
    __tablename__ = "broker_credentials"
    provider: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    encrypted_access_token: Mapped[str | None] = mapped_column(Text)
    encrypted_refresh_token: Mapped[str | None] = mapped_column(Text)
    access_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refresh_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refresh_lease_owner: Mapped[str | None] = mapped_column(String(36))
    refresh_lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CandleModel(Timestamped, Base):
    __tablename__ = "candles"
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float] = mapped_column(Float, nullable=False)
    dividend: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    split_ratio: Mapped[float] = mapped_column(Float, nullable=False, default=1)
    __table_args__ = (
        UniqueConstraint("symbol", "timeframe", "timestamp", name="uq_candle_series"),
        Index("ix_candles_lookup", "symbol", "timeframe", "timestamp"),
    )


class FeatureModel(Timestamped, Base):
    __tablename__ = "features"
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    values: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class OrderModel(Timestamped, Base):
    __tablename__ = "orders"
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    signal_id: Mapped[str | None] = mapped_column(String(36), unique=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    order_type: Mapped[str] = mapped_column(String(16), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    broker_response: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    broker_order_id: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)


class ManagedPositionModel(Timestamped, Base):
    __tablename__ = "managed_positions"
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    sector: Mapped[str] = mapped_column(String(100), nullable=False)
    uic: Mapped[int] = mapped_column(Integer, nullable=False)
    asset_type: Mapped[str] = mapped_column(String(32), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    atr: Mapped[float] = mapped_column(Float, nullable=False)
    stop_loss: Mapped[float] = mapped_column(Float, nullable=False)
    take_profit: Mapped[float] = mapped_column(Float, nullable=False)
    high_watermark: Mapped[float] = mapped_column(Float, nullable=False)
    broker_entry_order_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    broker_stop_order_id: Mapped[str | None] = mapped_column(String(100))
    broker_take_order_id: Mapped[str | None] = mapped_column(String(100))
    trailing_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    last_error: Mapped[str | None] = mapped_column(Text)


class PortfolioSnapshotModel(Timestamped, Base):
    __tablename__ = "portfolio_snapshots"
    equity: Mapped[float] = mapped_column(Float, nullable=False)
    cash: Mapped[float] = mapped_column(Float, nullable=False)
    gross_exposure: Mapped[float] = mapped_column(Float, nullable=False)
    sector_exposure: Mapped[dict[str, float]] = mapped_column(JSON, nullable=False)
    country_exposure: Mapped[dict[str, float]] = mapped_column(JSON, nullable=False)
    currency_exposure: Mapped[dict[str, float]] = mapped_column(JSON, nullable=False)
    positions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)


class PerformanceModel(Timestamped, Base):
    __tablename__ = "performance"
    period: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    realized_pnl: Mapped[float] = mapped_column(Float, nullable=False)
    unrealized_pnl: Mapped[float] = mapped_column(Float, nullable=False)
    sharpe: Mapped[float] = mapped_column(Float, nullable=False)
    sortino: Mapped[float] = mapped_column(Float, nullable=False)
    drawdown: Mapped[float] = mapped_column(Float, nullable=False)
    win_rate: Mapped[float] = mapped_column(Float, nullable=False)
    average_winner: Mapped[float] = mapped_column(Float, nullable=False)
    average_loser: Mapped[float] = mapped_column(Float, nullable=False)
    expectancy: Mapped[float] = mapped_column(Float, nullable=False)


class NewsModel(Timestamped, Base):
    __tablename__ = "news"
    external_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    symbol: Mapped[str | None] = mapped_column(String(32), index=True)
    headline: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sentiment: Mapped[str | None] = mapped_column(String(16))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class BacktestModel(Timestamped, Base):
    __tablename__ = "backtests"
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    data_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    data_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    data_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class AuditLogModel(Timestamped, Base):
    __tablename__ = "audit_logs"
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
