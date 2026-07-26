from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class Decision(StrEnum):
    BUY = "buy"
    HOLD = "hold"
    REJECT = "reject"


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"


class TradingMode(StrEnum):
    SIM = "sim"
    LIVE = "live"


@dataclass(frozen=True)
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    dividend: float = 0.0
    split_ratio: float = 1.0

    def __post_init__(self) -> None:
        values = (self.open, self.high, self.low, self.close, self.volume)
        if any(not math.isfinite(value) for value in values):
            raise ValueError("candle contains non-finite values")
        if min(self.open, self.high, self.low, self.close) <= 0:
            raise ValueError("prices must be positive")
        if self.volume < 0:
            raise ValueError("volume cannot be negative")
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("invalid OHLC relationship")
        if self.low > self.high:
            raise ValueError("low cannot exceed high")
        if self.split_ratio <= 0:
            raise ValueError("split ratio must be positive")


@dataclass(frozen=True)
class FeatureSet:
    as_of: datetime
    ema_12: float
    ema_26: float
    sma_20: float
    rsi_14: float
    atr_14: float
    macd: float
    macd_signal: float
    adx_14: float
    bollinger_upper: float
    bollinger_middle: float
    bollinger_lower: float
    volume_profile: float
    relative_strength: float
    volatility: float
    support: float
    resistance: float

    def to_safe_ai_payload(self) -> dict[str, float | str]:
        return {
            "as_of": self.as_of.astimezone(UTC).isoformat(),
            "ema_12": round(self.ema_12, 6),
            "ema_26": round(self.ema_26, 6),
            "sma_20": round(self.sma_20, 6),
            "rsi_14": round(self.rsi_14, 4),
            "atr_14": round(self.atr_14, 6),
            "macd": round(self.macd, 6),
            "macd_signal": round(self.macd_signal, 6),
            "adx_14": round(self.adx_14, 4),
            "bollinger_upper": round(self.bollinger_upper, 6),
            "bollinger_middle": round(self.bollinger_middle, 6),
            "bollinger_lower": round(self.bollinger_lower, 6),
            "volume_profile": round(self.volume_profile, 6),
            "relative_strength": round(self.relative_strength, 6),
            "volatility": round(self.volatility, 6),
            "support": round(self.support, 6),
            "resistance": round(self.resistance, 6),
        }


@dataclass(frozen=True)
class AIAnalysis:
    decision: Decision
    confidence: float
    reason: str
    needs_sol_review: bool = False
    risk_flags: tuple[str, ...] = ()
    model_tier: str = "none"
    provider: str = "none"

    def __post_init__(self) -> None:
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if len(self.reason) > 2_000:
            raise ValueError("reason is too long")


@dataclass(frozen=True)
class PortfolioState:
    equity: float
    cash: float
    peak_equity: float
    daily_pnl: float
    open_positions: int
    gross_exposure: float
    sector_exposure: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.equity <= 0 or self.peak_equity <= 0 or self.cash < 0:
            raise ValueError("portfolio balances are invalid")
        if self.open_positions < 0 or self.gross_exposure < 0:
            raise ValueError("portfolio exposure is invalid")

    @property
    def drawdown(self) -> float:
        return max(0.0, (self.peak_equity - self.equity) / self.peak_equity)

    @property
    def daily_loss_ratio(self) -> float:
        return max(0.0, -self.daily_pnl / self.equity)


@dataclass(frozen=True)
class MarketSnapshot:
    symbol: str
    bid: float
    ask: float
    as_of: datetime
    data_quality_ok: bool
    delayed_minutes: int = 0

    def __post_init__(self) -> None:
        if not self.symbol or self.bid <= 0 or self.ask <= 0 or self.ask < self.bid:
            raise ValueError("invalid market snapshot")
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("market snapshot timestamp must include a timezone")
        if self.delayed_minutes < 0:
            raise ValueError("delayed minutes cannot be negative")

    @property
    def entry_price(self) -> float:
        return self.ask

    @property
    def spread_pct(self) -> float:
        mid = (self.bid + self.ask) / 2.0
        return (self.ask - self.bid) / mid if mid else 1.0


@dataclass(frozen=True)
class TradeProposal:
    symbol: str
    sector: str
    side: OrderSide
    entry_price: float
    stop_loss: float
    take_profit: float
    spread_pct: float
    days_to_earnings: int | None
    data_quality_ok: bool
    market_regime: str = "unknown"
    requested_quantity: int | None = None
    uic: int | None = None
    asset_type: str = "Stock"
    order_type: OrderType = OrderType.MARKET

    def __post_init__(self) -> None:
        if not self.symbol or len(self.symbol) > 32:
            raise ValueError("invalid symbol")
        if min(self.entry_price, self.stop_loss, self.take_profit) <= 0:
            raise ValueError("prices must be positive")
        if self.spread_pct < 0:
            raise ValueError("spread cannot be negative")


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    quantity: int
    risk_amount: float
    reasons: tuple[str, ...]
    stop_loss: float
    take_profit: float


@dataclass(frozen=True)
class BrokerOrder:
    idempotency_key: str
    account_key: str
    uic: int
    asset_type: str
    symbol: str
    side: OrderSide
    quantity: int
    order_type: OrderType
    entry_price: float
    stop_loss: float
    take_profit: float


@dataclass(frozen=True)
class BrokerOrderResult:
    broker_order_id: str
    related_order_ids: tuple[str, ...]
    status: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BacktestTrade:
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float
    return_pct: float
    exit_reason: str


@dataclass(frozen=True)
class BacktestResult:
    initial_cash: float
    final_equity: float
    total_return: float
    max_drawdown: float
    sharpe: float
    sortino: float
    win_rate: float
    average_winner: float
    average_loser: float
    expectancy: float
    trades: tuple[BacktestTrade, ...]
