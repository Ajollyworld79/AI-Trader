from __future__ import annotations

import math
from collections.abc import Sequence
from itertools import pairwise

import numpy as np

from app.domain.entities import Candle, FeatureSet

MIN_CANDLES = 60


def _require(values: Sequence[float], minimum: int, name: str) -> None:
    if len(values) < minimum:
        raise ValueError(f"{name} requires at least {minimum} observations")
    if any(not math.isfinite(value) for value in values):
        raise ValueError(f"{name} received non-finite observations")


def sma(values: Sequence[float], period: int) -> float:
    _require(values, period, "SMA")
    return float(np.mean(np.asarray(values[-period:], dtype=float)))


def ema_series(values: Sequence[float], period: int) -> np.ndarray:
    _require(values, period, "EMA")
    array = np.asarray(values, dtype=float)
    result = np.empty_like(array)
    result[0] = array[0]
    alpha = 2.0 / (period + 1.0)
    for index in range(1, len(array)):
        result[index] = alpha * array[index] + (1.0 - alpha) * result[index - 1]
    return result


def ema(values: Sequence[float], period: int) -> float:
    return float(ema_series(values, period)[-1])


def rsi(values: Sequence[float], period: int = 14) -> float:
    _require(values, period + 1, "RSI")
    changes = np.diff(np.asarray(values, dtype=float))
    seed = changes[:period]
    average_gain = float(np.mean(np.maximum(seed, 0.0)))
    average_loss = float(np.mean(np.maximum(-seed, 0.0)))
    for change in changes[period:]:
        average_gain = ((period - 1) * average_gain + max(float(change), 0.0)) / period
        average_loss = ((period - 1) * average_loss + max(float(-change), 0.0)) / period
    if average_loss == 0:
        return 100.0 if average_gain > 0 else 50.0
    relative = average_gain / average_loss
    return 100.0 - (100.0 / (1.0 + relative))


def true_ranges(candles: Sequence[Candle]) -> np.ndarray:
    if len(candles) < 2:
        raise ValueError("true range requires two candles")
    values: list[float] = []
    for previous, current in pairwise(candles):
        values.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    return np.asarray(values, dtype=float)


def atr(candles: Sequence[Candle], period: int = 14) -> float:
    ranges = true_ranges(candles)
    _require(ranges.tolist(), period, "ATR")
    value = float(np.mean(ranges[:period]))
    for item in ranges[period:]:
        value = ((period - 1) * value + float(item)) / period
    return value


def macd(values: Sequence[float]) -> tuple[float, float]:
    _require(values, 35, "MACD")
    fast = ema_series(values, 12)
    slow = ema_series(values, 26)
    line = fast - slow
    signal = ema_series(line.tolist(), 9)
    return float(line[-1]), float(signal[-1])


def adx(candles: Sequence[Candle], period: int = 14) -> float:
    if len(candles) < period * 2 + 1:
        raise ValueError("ADX requires at least 29 candles")
    tr = true_ranges(candles)
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    for previous, current in pairwise(candles):
        up = current.high - previous.high
        down = previous.low - current.low
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
    atr_smooth = float(np.sum(tr[:period]))
    plus_smooth = float(np.sum(plus_dm[:period]))
    minus_smooth = float(np.sum(minus_dm[:period]))
    dx_values: list[float] = []
    for index in range(period, len(tr)):
        atr_smooth = atr_smooth - atr_smooth / period + float(tr[index])
        plus_smooth = plus_smooth - plus_smooth / period + plus_dm[index]
        minus_smooth = minus_smooth - minus_smooth / period + minus_dm[index]
        plus_di = 100 * plus_smooth / atr_smooth if atr_smooth else 0.0
        minus_di = 100 * minus_smooth / atr_smooth if atr_smooth else 0.0
        denominator = plus_di + minus_di
        dx_values.append(100 * abs(plus_di - minus_di) / denominator if denominator else 0.0)
    _require(dx_values, period, "ADX smoothing")
    return float(np.mean(dx_values[-period:]))


def bollinger(
    values: Sequence[float], period: int = 20, deviations: float = 2.0
) -> tuple[float, float, float]:
    _require(values, period, "Bollinger Bands")
    window = np.asarray(values[-period:], dtype=float)
    middle = float(np.mean(window))
    deviation = float(np.std(window, ddof=0))
    return middle + deviations * deviation, middle, middle - deviations * deviation


def annualized_volatility(values: Sequence[float], periods_per_year: int = 252) -> float:
    _require(values, 21, "volatility")
    array = np.asarray(values, dtype=float)
    returns = np.diff(np.log(array))
    return float(np.std(returns[-20:], ddof=1) * math.sqrt(periods_per_year))


def volume_weighted_price(candles: Sequence[Candle], period: int = 20) -> float:
    if len(candles) < period:
        raise ValueError("volume profile requires more candles")
    selected = candles[-period:]
    total_volume = sum(item.volume for item in selected)
    if total_volume == 0:
        return sum(item.close for item in selected) / period
    return sum(item.close * item.volume for item in selected) / total_volume


def compute_features(
    candles: Sequence[Candle], benchmark_closes: Sequence[float] | None = None
) -> FeatureSet:
    if len(candles) < MIN_CANDLES:
        raise ValueError(f"feature calculation requires at least {MIN_CANDLES} candles")
    ordered = sorted(candles, key=lambda item: item.timestamp)
    if list(candles) != ordered:
        raise ValueError("candles must be in ascending timestamp order")
    if len({item.timestamp for item in candles}) != len(candles):
        raise ValueError("duplicate candle timestamps are forbidden")

    closes = [item.close for item in candles]
    macd_line, macd_signal = macd(closes)
    upper, middle, lower = bollinger(closes)
    benchmark_strength = 0.0
    if benchmark_closes is not None:
        _require(benchmark_closes, 21, "relative strength benchmark")
        stock_return = closes[-1] / closes[-21] - 1
        benchmark_return = benchmark_closes[-1] / benchmark_closes[-21] - 1
        benchmark_strength = stock_return - benchmark_return
    support = min(item.low for item in candles[-20:])
    resistance = max(item.high for item in candles[-20:])
    return FeatureSet(
        as_of=candles[-1].timestamp,
        ema_12=ema(closes, 12),
        ema_26=ema(closes, 26),
        sma_20=sma(closes, 20),
        rsi_14=rsi(closes),
        atr_14=atr(candles),
        macd=macd_line,
        macd_signal=macd_signal,
        adx_14=adx(candles),
        bollinger_upper=upper,
        bollinger_middle=middle,
        bollinger_lower=lower,
        volume_profile=volume_weighted_price(candles),
        relative_strength=benchmark_strength,
        volatility=annualized_volatility(closes),
        support=support,
        resistance=resistance,
    )


def is_interesting(features: FeatureSet, latest_price: float) -> bool:
    trend = features.ema_12 > features.ema_26 and latest_price > features.sma_20
    momentum = 45 <= features.rsi_14 <= 72 and features.macd > features.macd_signal
    quality = features.adx_14 >= 18 and features.volatility <= 0.80
    room = latest_price < features.resistance * 1.02
    return trend and momentum and quality and room
