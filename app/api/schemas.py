from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.domain.entities import Candle, OrderSide, OrderType, PortfolioState, TradeProposal

Symbol = Annotated[
    str,
    StringConstraints(strip_whitespace=True, to_upper=True, pattern=r"^[A-Z0-9.-]{1,32}$"),
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CandleInput(StrictModel):
    timestamp: datetime
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)
    dividend: float = Field(default=0, ge=0)
    split_ratio: float = Field(default=1, gt=0)

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value

    def to_domain(self) -> Candle:
        return Candle(**self.model_dump())


class AnalysisRequest(StrictModel):
    symbol: Symbol
    candles: list[CandleInput] = Field(min_length=60, max_length=5_000)
    benchmark_closes: list[float] | None = Field(default=None, min_length=21, max_length=5_000)
    news: list[str] = Field(default_factory=list, max_length=20)
    sector: str = Field(default="Unknown", min_length=1, max_length=100)
    uic: int | None = Field(default=None, ge=1)
    asset_type: str = Field(default="Stock", pattern=r"^[A-Za-z]{2,32}$")
    days_to_earnings: int | None = Field(default=None, ge=0, le=365)

    @field_validator("news")
    @classmethod
    def limit_news(cls, values: list[str]) -> list[str]:
        if any(len(value) > 2_000 for value in values):
            raise ValueError("news entries cannot exceed 2,000 characters")
        return values


class AnalysisResponse(StrictModel):
    signal_id: str
    features: dict[str, float | str]
    decision: Literal["buy", "hold", "reject"]
    confidence: float
    reason: str
    needs_sol_review: bool
    risk_flags: list[str]
    model_tier: str
    provider: str


class PortfolioInput(StrictModel):
    equity: float = Field(gt=0)
    cash: float = Field(ge=0)
    peak_equity: float = Field(gt=0)
    daily_pnl: float
    open_positions: int = Field(ge=0)
    gross_exposure: float = Field(ge=0)
    sector_exposure: dict[str, float] = Field(default_factory=dict)

    @field_validator("sector_exposure")
    @classmethod
    def validate_sector_exposure(cls, values: dict[str, float]) -> dict[str, float]:
        if len(values) > 100 or any(value < 0 for value in values.values()):
            raise ValueError("invalid sector exposure")
        return values

    def to_domain(self) -> PortfolioState:
        return PortfolioState(**self.model_dump())


class TradeProposalInput(StrictModel):
    symbol: Symbol
    sector: str = Field(min_length=1, max_length=100)
    side: OrderSide = OrderSide.BUY
    entry_price: float = Field(gt=0)
    stop_loss: float = Field(gt=0)
    take_profit: float = Field(gt=0)
    spread_pct: float = Field(ge=0, le=0.10)
    days_to_earnings: int | None = Field(default=None, ge=0, le=365)
    data_quality_ok: bool
    requested_quantity: int | None = Field(default=None, ge=1, le=10_000_000)
    uic: int | None = Field(default=None, ge=1)
    asset_type: str = Field(default="Stock", pattern=r"^[A-Za-z]{2,32}$")
    order_type: OrderType = OrderType.MARKET

    def to_domain(self) -> TradeProposal:
        return TradeProposal(**self.model_dump())


class RiskRequest(StrictModel):
    proposal: TradeProposalInput
    portfolio: PortfolioInput


class RiskResponse(StrictModel):
    approved: bool
    quantity: int
    risk_amount: float
    reasons: list[str]
    stop_loss: float
    take_profit: float


class OrderRequest(StrictModel):
    signal_id: str = Field(min_length=36, max_length=36)
    requested_quantity: int | None = Field(default=None, ge=1, le=10_000_000)
    order_type: OrderType = OrderType.MARKET


class OrderResponse(StrictModel):
    broker_order_id: str
    related_order_ids: list[str]
    status: str


class BacktestConfigInput(StrictModel):
    initial_cash: float = Field(default=100_000, gt=0, le=1_000_000_000)
    commission_pct: float = Field(default=0.001, ge=0, le=0.10)
    spread_pct: float = Field(default=0.001, ge=0, le=0.10)
    slippage_pct: float = Field(default=0.0005, ge=0, le=0.10)
    risk_per_trade: float = Field(default=0.005, gt=0, le=0.02)
    atr_stop_multiple: float = Field(default=2.0, gt=0, le=10)
    reward_risk_ratio: float = Field(default=2.0, gt=0, le=20)
    max_holding_bars: int = Field(default=40, ge=1, le=5_000)


class BacktestRequest(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    symbol: Symbol
    candles: list[CandleInput] = Field(min_length=62, max_length=10_000)
    config: BacktestConfigInput = Field(default_factory=BacktestConfigInput)


class BacktestResponse(StrictModel):
    id: str
    result: dict[str, Any]


class SystemStatusResponse(StrictModel):
    environment: str
    trading_mode: str
    broker_backend: str
    trading_kill_switch: bool
    ai_enabled: bool
    database_healthy: bool
    cache_healthy: bool


class DashboardResponse(StrictModel):
    ai_usage: dict[str, float | int]
    portfolio: dict[str, float | int]
    ai_budget_dkk: float
    trading_mode: str
    trading_kill_switch: bool
