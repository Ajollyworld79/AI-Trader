from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Annotated, Any, cast

from fastapi import APIRouter, Header, HTTPException, Query, Request, status

from app.api.deps import AdminPrincipal, ApiPrincipal, get_container
from app.api.schemas import (
    AnalysisRequest,
    AnalysisResponse,
    BacktestRequest,
    BacktestResponse,
    DashboardResponse,
    OrderRequest,
    OrderResponse,
    RiskRequest,
    RiskResponse,
    SystemStatusResponse,
)
from app.broker.base import BrokerError
from app.domain.backtest import BacktestConfig, BacktestEngine
from app.infrastructure.repositories import DuplicateOrderError
from app.services.trading import LiveConfirmationError, TradingDisabledError

router = APIRouter(prefix="/v1")


@router.get("/system/status", response_model=SystemStatusResponse)
async def system_status(request: Request, _principal: ApiPrincipal) -> SystemStatusResponse:
    container = get_container(request)
    return SystemStatusResponse(
        environment=container.settings.environment,
        trading_mode=container.settings.trading_mode,
        broker_backend=container.settings.broker_backend,
        trading_kill_switch=container.settings.trading_kill_switch,
        ai_enabled=container.analyzer.enabled,
        database_healthy=container.database.ping(),
        cache_healthy=await container.rate_limiter.healthy(),
    )


@router.post("/analysis", response_model=AnalysisResponse)
async def analyze_market(
    body: AnalysisRequest, request: Request, _principal: ApiPrincipal
) -> AnalysisResponse:
    container = get_container(request)
    try:
        features, analysis, signal_id = await container.analysis_service.analyze(
            symbol=body.symbol,
            candles=[item.to_domain() for item in body.candles],
            benchmark_closes=body.benchmark_closes,
            news=body.news,
            sector=body.sector,
            uic=body.uic,
            asset_type=body.asset_type,
            days_to_earnings=body.days_to_earnings,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return AnalysisResponse(
        signal_id=signal_id,
        features=features.to_safe_ai_payload(),
        decision=analysis.decision.value,
        confidence=analysis.confidence,
        reason=analysis.reason,
        needs_sol_review=analysis.needs_sol_review,
        risk_flags=list(analysis.risk_flags),
        model_tier=analysis.model_tier,
        provider=analysis.provider,
    )


@router.post("/risk/evaluate", response_model=RiskResponse)
async def evaluate_risk(
    body: RiskRequest, request: Request, _principal: ApiPrincipal
) -> RiskResponse:
    decision = get_container(request).risk_engine.evaluate(
        body.proposal.to_domain(), body.portfolio.to_domain()
    )
    return RiskResponse(
        approved=decision.approved,
        quantity=decision.quantity,
        risk_amount=decision.risk_amount,
        reasons=list(decision.reasons),
        stop_loss=decision.stop_loss,
        take_profit=decision.take_profit,
    )


@router.post(
    "/orders",
    response_model=OrderResponse,
    status_code=status.HTTP_201_CREATED,
)
async def execute_order(
    body: OrderRequest,
    request: Request,
    principal: AdminPrincipal,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=64)],
    x_live_confirmation: Annotated[str | None, Header()] = None,
) -> OrderResponse:
    container = get_container(request)
    if not idempotency_key.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(status_code=400, detail="invalid idempotency key")
    try:
        result = await container.trading_service.execute_signal(
            signal_id=body.signal_id,
            requested_quantity=body.requested_quantity,
            order_type=body.order_type,
            idempotency_key=idempotency_key,
            actor_key=principal.raw_key,
            correlation_id=request.headers.get("X-Request-ID", ""),
            live_confirmation=x_live_confirmation,
        )
    except DuplicateOrderError as exc:
        raise HTTPException(status_code=409, detail="duplicate order request") from exc
    except (TradingDisabledError, LiveConfirmationError) as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (ValueError, BrokerError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return OrderResponse(
        broker_order_id=result.broker_order_id,
        related_order_ids=list(result.related_order_ids),
        status=result.status,
    )


@router.get("/broker/account")
async def broker_account(request: Request, _principal: AdminPrincipal) -> dict[str, object]:
    try:
        return cast(dict[str, object], await get_container(request).broker.account_info())
    except BrokerError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/broker/portfolio")
async def broker_portfolio(request: Request, _principal: AdminPrincipal) -> dict[str, object]:
    try:
        return cast(dict[str, object], await get_container(request).broker.portfolio())
    except BrokerError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/broker/auth/start")
async def saxo_auth_start(request: Request, _principal: AdminPrincipal) -> dict[str, str]:
    container = get_container(request)
    if container.settings.broker_backend != "saxo":
        raise HTTPException(status_code=409, detail="Saxo broker is not enabled")
    state = container.create_oauth_state()
    return {"authorization_url": container.broker.tokens.authorization_url(state)}


@router.get("/broker/auth/callback", include_in_schema=False)
async def saxo_auth_callback(
    request: Request,
    code: Annotated[str, Query(min_length=8, max_length=2_048)],
    state_value: Annotated[str, Query(alias="state", min_length=32, max_length=256)],
) -> dict[str, str]:
    container = get_container(request)
    if not container.consume_oauth_state(state_value):
        raise HTTPException(status_code=400, detail="invalid or expired OAuth state")
    try:
        await container.broker.tokens.exchange_code(code)
    except BrokerError as exc:
        raise HTTPException(status_code=400, detail="Saxo authentication failed") from exc
    return {"status": "connected", "message": "Saxo token accepted for this running service"}


@router.post("/backtests", response_model=BacktestResponse)
async def run_backtest(
    body: BacktestRequest, request: Request, _principal: AdminPrincipal
) -> BacktestResponse:
    container = get_container(request)
    candles = [item.to_domain() for item in body.candles]
    config = BacktestConfig(**body.config.model_dump())
    try:
        result = BacktestEngine().run(candles, config)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    result_payload: dict[str, Any] = asdict(result)
    result_payload["trades"] = [
        {
            **asdict(trade),
            "entry_time": trade.entry_time.isoformat(),
            "exit_time": trade.exit_time.isoformat(),
        }
        for trade in result.trades
    ]
    data_hash = hashlib.sha256(
        json.dumps([item.model_dump(mode="json") for item in body.candles], sort_keys=True).encode()
    ).hexdigest()
    record_id = container.repository.save_backtest(
        name=body.name,
        symbol=body.symbol,
        config=body.config.model_dump(mode="json"),
        result=result_payload,
        data_start=candles[0].timestamp,
        data_end=candles[-1].timestamp,
        data_hash=data_hash,
    )
    return BacktestResponse(id=record_id, result=result_payload)


@router.get("/dashboard", response_model=DashboardResponse)
async def dashboard(request: Request, _principal: ApiPrincipal) -> DashboardResponse:
    container = get_container(request)
    return DashboardResponse(
        ai_usage=container.repository.ai_usage(),
        portfolio=container.repository.dashboard_summary(),
        market_intelligence=container.repository.market_intelligence_summary(),
        ai_budget_dkk=container.settings.ai_monthly_budget_dkk,
        trading_mode=container.settings.trading_mode,
        trading_kill_switch=container.settings.trading_kill_switch,
    )
