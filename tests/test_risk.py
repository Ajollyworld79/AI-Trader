from __future__ import annotations

from dataclasses import replace

from app.config import Settings
from app.domain.entities import OrderSide, PortfolioState, TradeProposal
from app.domain.risk import RiskEngine


def portfolio(**overrides: object) -> PortfolioState:
    values: dict[str, object] = {
        "equity": 100_000.0,
        "cash": 80_000.0,
        "peak_equity": 100_000.0,
        "daily_pnl": 0.0,
        "open_positions": 2,
        "gross_exposure": 20_000.0,
        "sector_exposure": {"Technology": 5_000.0},
    }
    values.update(overrides)
    return PortfolioState(**values)  # type: ignore[arg-type]


def proposal(**overrides: object) -> TradeProposal:
    values: dict[str, object] = {
        "symbol": "MSFT",
        "sector": "Technology",
        "side": OrderSide.BUY,
        "entry_price": 100.0,
        "stop_loss": 95.0,
        "take_profit": 110.0,
        "spread_pct": 0.001,
        "days_to_earnings": 20,
        "data_quality_ok": True,
        "uic": 123,
    }
    values.update(overrides)
    return TradeProposal(**values)  # type: ignore[arg-type]


def test_risk_engine_sizes_position_to_half_percent(settings: Settings) -> None:
    decision = RiskEngine(settings).evaluate(proposal(), portfolio())
    assert decision.approved
    assert decision.quantity == 100
    assert decision.risk_amount == 500


def test_risk_engine_rejects_every_policy_boundary(settings: Settings) -> None:
    engine = RiskEngine(settings)
    cases = [
        (proposal(side=OrderSide.SELL), portfolio(), "short_selling_disabled"),
        (proposal(spread_pct=0.02), portfolio(), "spread_too_wide"),
        (proposal(days_to_earnings=1), portfolio(), "earnings_blackout"),
        (proposal(data_quality_ok=False), portfolio(), "data_quality_failure"),
        (proposal(), portfolio(open_positions=10), "max_open_positions"),
        (proposal(), portfolio(daily_pnl=-2_100), "max_daily_loss"),
        (proposal(), portfolio(equity=89_000), "max_drawdown"),
    ]
    for candidate, state, expected in cases:
        decision = engine.evaluate(candidate, state)
        assert not decision.approved
        assert expected in decision.reasons
        assert decision.quantity == 0


def test_requested_quantity_cannot_override_python_risk(settings: Settings) -> None:
    decision = RiskEngine(settings).evaluate(proposal(requested_quantity=10_000), portfolio())
    assert not decision.approved
    assert "requested_quantity_exceeds_risk_limit" in decision.reasons


def test_sector_exposure_is_enforced(settings: Settings) -> None:
    state = replace(portfolio(), sector_exposure={"Technology": 19_900.0})
    decision = RiskEngine(settings).evaluate(proposal(), state)
    assert not decision.approved
    assert "max_sector_exposure" in decision.reasons
