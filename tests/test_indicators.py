from __future__ import annotations

import math

import pytest

from app.domain.entities import Candle
from app.domain.indicators import (
    adx,
    annualized_volatility,
    atr,
    bollinger,
    compute_features,
    ema,
    is_interesting,
    macd,
    rsi,
    sma,
)
from tests.conftest import candle_series


def test_indicators_are_deterministic_and_finite() -> None:
    candles = candle_series()
    closes = [item.close for item in candles]
    features = compute_features(candles, [value * 0.999 for value in closes])
    assert features.as_of == candles[-1].timestamp
    assert all(
        math.isfinite(value)
        for value in (
            features.ema_12,
            features.rsi_14,
            features.atr_14,
            features.macd,
            features.adx_14,
            features.volatility,
        )
    )
    assert ema(closes, 12) > sma(closes, 20)
    assert 0 <= rsi(closes) <= 100
    assert atr(candles) > 0
    assert adx(candles) >= 0
    upper, middle, lower = bollinger(closes)
    assert upper >= middle >= lower
    line, signal = macd(closes)
    assert math.isfinite(line)
    assert math.isfinite(signal)
    assert annualized_volatility(closes) >= 0
    assert isinstance(is_interesting(features, closes[-1]), bool)


def test_candle_validation_and_series_ordering() -> None:
    candles = candle_series()
    with pytest.raises(ValueError):
        Candle(
            timestamp=candles[0].timestamp,
            open=100,
            high=90,
            low=80,
            close=100,
            volume=1,
        )
    with pytest.raises(ValueError, match="ascending"):
        compute_features(list(reversed(candles)))
    duplicate = candles.copy()
    duplicate[-1] = duplicate[-2]
    with pytest.raises(ValueError, match="duplicate"):
        compute_features(duplicate)


def test_short_series_are_rejected() -> None:
    with pytest.raises(ValueError):
        compute_features(candle_series(20))
