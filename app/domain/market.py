from __future__ import annotations

from dataclasses import dataclass

from app.domain.entities import Candle
from app.domain.indicators import compute_features


@dataclass(frozen=True)
class MarketRegime:
    regime: str
    close: float
    volatility: float
    adx: float
    data_quality_ok: bool

    def to_payload(self) -> dict[str, float | str | bool]:
        return {
            "regime": self.regime,
            "close": round(self.close, 6),
            "volatility": round(self.volatility, 6),
            "adx": round(self.adx, 4),
            "data_quality_ok": self.data_quality_ok,
        }


def classify_market_regime(candles: list[Candle], *, extreme_volatility: float) -> MarketRegime:
    features = compute_features(candles)
    close = candles[-1].close
    if features.volatility >= extreme_volatility:
        regime = "extreme_volatility"
    elif close > features.sma_20 and features.ema_12 > features.ema_26 and features.adx_14 >= 20:
        regime = "bullish_trending"
    elif close < features.sma_20 and features.ema_12 < features.ema_26 and features.adx_14 >= 20:
        regime = "bearish_trending"
    else:
        regime = "sideways"
    return MarketRegime(
        regime=regime,
        close=close,
        volatility=features.volatility,
        adx=features.adx_14,
        data_quality_ok=True,
    )
