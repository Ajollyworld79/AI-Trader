from __future__ import annotations

import math

from app.config import Settings
from app.domain.entities import OrderSide, PortfolioState, RiskDecision, TradeProposal


class RiskEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def evaluate(self, proposal: TradeProposal, portfolio: PortfolioState) -> RiskDecision:
        reasons: list[str] = []
        if proposal.side is not OrderSide.BUY:
            reasons.append("short_selling_disabled")
        if proposal.stop_loss >= proposal.entry_price:
            reasons.append("stop_must_be_below_entry")
        if proposal.take_profit <= proposal.entry_price:
            reasons.append("take_profit_must_be_above_entry")
        if not proposal.data_quality_ok:
            reasons.append("data_quality_failure")
        if proposal.spread_pct > self.settings.max_spread_pct:
            reasons.append("spread_too_wide")
        if (
            proposal.days_to_earnings is not None
            and proposal.days_to_earnings <= self.settings.earnings_blackout_days
        ):
            reasons.append("earnings_blackout")
        if portfolio.open_positions >= self.settings.max_open_positions:
            reasons.append("max_open_positions")
        if portfolio.daily_loss_ratio >= self.settings.max_daily_loss:
            reasons.append("max_daily_loss")
        if portfolio.drawdown >= self.settings.max_drawdown:
            reasons.append("max_drawdown")

        risk_per_share = proposal.entry_price - proposal.stop_loss
        if risk_per_share <= 0:
            quantity = 0
            risk_amount = 0.0
        else:
            risk_budget = portfolio.equity * self.settings.max_risk_per_trade
            risk_quantity = math.floor(risk_budget / risk_per_share)
            cash_quantity = math.floor(portfolio.cash / proposal.entry_price)
            quantity = max(0, min(risk_quantity, cash_quantity))
            if proposal.requested_quantity is not None:
                if proposal.requested_quantity > quantity:
                    reasons.append("requested_quantity_exceeds_risk_limit")
                quantity = min(quantity, proposal.requested_quantity)
            risk_amount = quantity * risk_per_share

        current_sector = portfolio.sector_exposure.get(proposal.sector, 0.0)
        proposed_sector = current_sector + quantity * proposal.entry_price
        if proposed_sector / portfolio.equity > self.settings.max_sector_exposure:
            reasons.append("max_sector_exposure")
        if quantity < 1:
            reasons.append("insufficient_risk_or_cash_budget")
        if quantity * proposal.entry_price > portfolio.cash:
            reasons.append("leverage_forbidden")

        return RiskDecision(
            approved=not reasons,
            quantity=quantity if not reasons else 0,
            risk_amount=risk_amount if not reasons else 0.0,
            reasons=tuple(reasons),
            stop_loss=proposal.stop_loss,
            take_profit=proposal.take_profit,
        )
