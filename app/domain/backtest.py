from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from app.domain.entities import BacktestResult, BacktestTrade, Candle
from app.domain.indicators import MIN_CANDLES, compute_features, is_interesting
from app.domain.portfolio import max_drawdown, sharpe_ratio, sortino_ratio, trade_statistics


@dataclass(frozen=True)
class BacktestConfig:
    initial_cash: float = 100_000.0
    commission_pct: float = 0.001
    spread_pct: float = 0.001
    slippage_pct: float = 0.0005
    risk_per_trade: float = 0.005
    atr_stop_multiple: float = 2.0
    reward_risk_ratio: float = 2.0
    max_holding_bars: int = 40

    def __post_init__(self) -> None:
        if self.initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        costs = (self.commission_pct, self.spread_pct, self.slippage_pct)
        if any(value < 0 or value > 0.10 for value in costs):
            raise ValueError("trading costs must be between 0 and 10%")
        if not 0 < self.risk_per_trade <= 0.02:
            raise ValueError("risk_per_trade must be between 0 and 2%")


@dataclass
class _Position:
    entry_index: int
    entry_price: float
    quantity: int
    stop: float
    target: float


class BacktestEngine:
    """Event-driven long-only backtester.

    A signal may only inspect bars before the execution bar. Orders are executed
    at the next bar's open. If stop and target are both touched in one bar, the
    stop wins, which intentionally avoids optimistic look-ahead assumptions.
    """

    def run(self, candles: Sequence[Candle], config: BacktestConfig) -> BacktestResult:
        if len(candles) < MIN_CANDLES + 2:
            raise ValueError("backtest requires at least 62 candles")
        ordered = sorted(candles, key=lambda item: item.timestamp)
        if list(candles) != ordered:
            raise ValueError("candles must be sorted")
        if len({item.timestamp for item in candles}) != len(candles):
            raise ValueError("duplicate timestamps are forbidden")

        cash = config.initial_cash
        equity_curve: list[float] = [cash]
        period_returns: list[float] = []
        trades: list[BacktestTrade] = []
        position: _Position | None = None

        for index in range(MIN_CANDLES, len(candles)):
            bar = candles[index]
            previous_equity = equity_curve[-1]

            if position is not None:
                position = self._apply_corporate_actions(position, bar)
                cash += bar.dividend * position.quantity
                exit_price: float | None = None
                exit_reason = ""
                if bar.low <= position.stop:
                    exit_price = self._sell_fill(position.stop, config)
                    exit_reason = "stop_loss"
                elif bar.high >= position.target:
                    exit_price = self._sell_fill(position.target, config)
                    exit_reason = "take_profit"
                elif index - position.entry_index >= config.max_holding_bars:
                    exit_price = self._sell_fill(bar.open, config)
                    exit_reason = "time_exit"

                if exit_price is not None:
                    gross = exit_price * position.quantity
                    commission = gross * config.commission_pct
                    cash += gross - commission
                    cost_basis = position.entry_price * position.quantity
                    pnl = gross - commission - cost_basis
                    trades.append(
                        BacktestTrade(
                            entry_time=candles[position.entry_index].timestamp,
                            exit_time=bar.timestamp,
                            entry_price=position.entry_price,
                            exit_price=exit_price,
                            quantity=position.quantity,
                            pnl=pnl,
                            return_pct=pnl / cost_basis,
                            exit_reason=exit_reason,
                        )
                    )
                    position = None

            if position is None:
                # History ends at index - 1; this bar is never visible to signal generation.
                history = candles[:index]
                features = compute_features(history)
                if is_interesting(features, history[-1].close):
                    entry_price = self._buy_fill(bar.open, config)
                    stop = entry_price - features.atr_14 * config.atr_stop_multiple
                    risk_per_share = entry_price - stop
                    risk_budget = cash * config.risk_per_trade
                    quantity = min(
                        math.floor(risk_budget / risk_per_share) if risk_per_share > 0 else 0,
                        math.floor(cash / (entry_price * (1 + config.commission_pct))),
                    )
                    if quantity > 0 and stop > 0:
                        cost = entry_price * quantity
                        cash -= cost + cost * config.commission_pct
                        position = _Position(
                            entry_index=index,
                            entry_price=entry_price,
                            quantity=quantity,
                            stop=stop,
                            target=entry_price + risk_per_share * config.reward_risk_ratio,
                        )

            marked_equity = cash
            if position is not None:
                marked_equity += position.quantity * bar.close
            equity_curve.append(marked_equity)
            period_returns.append(
                (marked_equity / previous_equity - 1.0) if previous_equity else 0.0
            )

        if position is not None:
            bar = candles[-1]
            exit_price = self._sell_fill(bar.close, config)
            gross = exit_price * position.quantity
            commission = gross * config.commission_pct
            cash += gross - commission
            cost_basis = position.entry_price * position.quantity
            pnl = gross - commission - cost_basis
            trades.append(
                BacktestTrade(
                    entry_time=candles[position.entry_index].timestamp,
                    exit_time=bar.timestamp,
                    entry_price=position.entry_price,
                    exit_price=exit_price,
                    quantity=position.quantity,
                    pnl=pnl,
                    return_pct=pnl / cost_basis,
                    exit_reason="end_of_data",
                )
            )
            equity_curve[-1] = cash

        pnls = [trade.pnl for trade in trades]
        win_rate, average_winner, average_loser, expectancy = trade_statistics(pnls)
        return BacktestResult(
            initial_cash=config.initial_cash,
            final_equity=cash,
            total_return=cash / config.initial_cash - 1.0,
            max_drawdown=max_drawdown(equity_curve),
            sharpe=sharpe_ratio(period_returns),
            sortino=sortino_ratio(period_returns),
            win_rate=win_rate,
            average_winner=average_winner,
            average_loser=average_loser,
            expectancy=expectancy,
            trades=tuple(trades),
        )

    @staticmethod
    def _buy_fill(price: float, config: BacktestConfig) -> float:
        return price * (1.0 + config.spread_pct / 2.0 + config.slippage_pct)

    @staticmethod
    def _sell_fill(price: float, config: BacktestConfig) -> float:
        return price * (1.0 - config.spread_pct / 2.0 - config.slippage_pct)

    @staticmethod
    def _apply_corporate_actions(position: _Position, bar: Candle) -> _Position:
        if bar.split_ratio == 1.0:
            return position
        return _Position(
            entry_index=position.entry_index,
            entry_price=position.entry_price / bar.split_ratio,
            quantity=math.floor(position.quantity * bar.split_ratio),
            stop=position.stop / bar.split_ratio,
            target=position.target / bar.split_ratio,
        )


def walk_forward_slices(
    candles: Sequence[Candle], train_size: int, test_size: int
) -> tuple[tuple[Sequence[Candle], Sequence[Candle]], ...]:
    if train_size < MIN_CANDLES or test_size < 2:
        raise ValueError("invalid walk-forward window")
    slices: list[tuple[Sequence[Candle], Sequence[Candle]]] = []
    start = 0
    while start + train_size + test_size <= len(candles):
        train = candles[start : start + train_size]
        test = candles[start + train_size : start + train_size + test_size]
        slices.append((train, test))
        start += test_size
    return tuple(slices)
