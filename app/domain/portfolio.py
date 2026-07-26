from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np


def max_drawdown(equity_curve: Sequence[float]) -> float:
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    worst = 0.0
    for equity in equity_curve:
        peak = max(peak, equity)
        if peak > 0:
            worst = max(worst, (peak - equity) / peak)
    return worst


def sharpe_ratio(returns: Sequence[float], periods_per_year: int = 252) -> float:
    if len(returns) < 2:
        return 0.0
    deviation = float(np.std(returns, ddof=1))
    if deviation == 0:
        return 0.0
    return float(np.mean(returns) / deviation * math.sqrt(periods_per_year))


def sortino_ratio(returns: Sequence[float], periods_per_year: int = 252) -> float:
    if len(returns) < 2:
        return 0.0
    downside = np.asarray([value for value in returns if value < 0], dtype=float)
    if len(downside) == 0:
        return 0.0
    deviation = float(np.sqrt(np.mean(np.square(downside))))
    if deviation == 0:
        return 0.0
    return float(np.mean(returns) / deviation * math.sqrt(periods_per_year))


def trade_statistics(pnls: Sequence[float]) -> tuple[float, float, float, float]:
    if not pnls:
        return 0.0, 0.0, 0.0, 0.0
    winners = [value for value in pnls if value > 0]
    losers = [value for value in pnls if value < 0]
    win_rate = len(winners) / len(pnls)
    average_winner = sum(winners) / len(winners) if winners else 0.0
    average_loser = sum(losers) / len(losers) if losers else 0.0
    expectancy = win_rate * average_winner + (1 - win_rate) * average_loser
    return win_rate, average_winner, average_loser, expectancy
