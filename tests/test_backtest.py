from __future__ import annotations

from dataclasses import replace

import pytest

import app.domain.backtest as backtest_module
from app.domain.backtest import BacktestConfig, BacktestEngine, walk_forward_slices
from app.domain.entities import FeatureSet
from tests.conftest import candle_series


def test_backtest_runs_with_costs_and_reproducible_metrics() -> None:
    candles = candle_series(180)
    config = BacktestConfig()
    first = BacktestEngine().run(candles, config)
    second = BacktestEngine().run(candles, config)
    assert first == second
    assert first.initial_cash == 100_000
    assert first.final_equity > 0
    assert 0 <= first.max_drawdown <= 1


def test_signal_never_sees_execution_bar(monkeypatch: object) -> None:
    candles = candle_series(70)
    seen_lengths: list[int] = []

    def fake_features(history: object) -> FeatureSet:
        items = list(history)  # type: ignore[arg-type]
        seen_lengths.append(len(items))
        return FeatureSet(
            as_of=items[-1].timestamp,
            ema_12=2,
            ema_26=1,
            sma_20=1,
            rsi_14=55,
            atr_14=1,
            macd=2,
            macd_signal=1,
            adx_14=25,
            bollinger_upper=200,
            bollinger_middle=100,
            bollinger_lower=50,
            volume_profile=100,
            relative_strength=0.1,
            volatility=0.1,
            support=90,
            resistance=200,
        )

    monkeypatch.setattr(backtest_module, "compute_features", fake_features)  # type: ignore[attr-defined]
    monkeypatch.setattr(backtest_module, "is_interesting", lambda *_: True)  # type: ignore[attr-defined]
    result = BacktestEngine().run(candles, BacktestConfig(max_holding_bars=1))
    assert seen_lengths
    assert seen_lengths[0] == 60
    assert result.trades[0].entry_time == candles[60].timestamp


def test_walk_forward_produces_disjoint_train_test_windows() -> None:
    candles = candle_series(200)
    windows = walk_forward_slices(candles, train_size=60, test_size=20)
    assert len(windows) == 7
    for train, test in windows:
        assert train[-1].timestamp < test[0].timestamp


def test_dividend_is_added_once_while_position_is_open(monkeypatch: object) -> None:
    candles = candle_series(65)
    monkeypatch.setattr(backtest_module, "is_interesting", lambda *_: True)  # type: ignore[attr-defined]
    config = BacktestConfig(
        commission_pct=0,
        spread_pct=0,
        slippage_pct=0,
        max_holding_bars=100,
    )
    without_dividend = BacktestEngine().run(candles, config)
    with_dividend_candles = list(candles)
    with_dividend_candles[61] = replace(with_dividend_candles[61], dividend=1.25)
    with_dividend = BacktestEngine().run(with_dividend_candles, config)
    quantity = with_dividend.trades[0].quantity
    assert with_dividend.final_equity - without_dividend.final_equity == pytest.approx(
        quantity * 1.25
    )
