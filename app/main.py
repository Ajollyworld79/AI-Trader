from __future__ import annotations

import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import cast

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.ai.client import RoutedMarketAnalyzer
from app.api.routes import router
from app.broker.base import Broker
from app.broker.saxo import SaxoBroker
from app.broker.simulated import SimulatedBroker
from app.config import Settings, get_settings
from app.domain.risk import RiskEngine
from app.infrastructure.database import Base, Database
from app.infrastructure.rate_limit import RateLimiter
from app.infrastructure.repositories import Repository
from app.logging import configure_logging
from app.middleware import RequestContextMiddleware, SecurityHeadersMiddleware
from app.services.analysis import AnalysisService
from app.services.positions import PositionManager
from app.services.trading import TradingService

logger = structlog.get_logger()


@dataclass
class Container:
    settings: Settings
    database: Database
    repository: Repository
    rate_limiter: RateLimiter
    analyzer: RoutedMarketAnalyzer
    risk_engine: RiskEngine
    broker: Broker
    analysis_service: AnalysisService
    trading_service: TradingService
    position_manager: PositionManager
    oauth_states: dict[str, float] = field(default_factory=dict)

    def create_oauth_state(self) -> str:
        now = time.monotonic()
        self.oauth_states = {
            key: expires for key, expires in self.oauth_states.items() if expires > now
        }
        state = secrets.token_urlsafe(32)
        self.oauth_states[state] = now + 600
        return state

    def consume_oauth_state(self, state: str) -> bool:
        expires = self.oauth_states.pop(state, 0)
        return expires > time.monotonic()


def build_container(settings: Settings) -> Container:
    database = Database(settings)
    if settings.environment != "production":
        Base.metadata.create_all(database.engine)
    repository = Repository(database)
    rate_limiter = RateLimiter(settings.redis_url, settings.rate_limit_per_minute)
    analyzer = RoutedMarketAnalyzer(settings, repository)
    risk_engine = RiskEngine(settings)
    broker: Broker = (
        SaxoBroker(settings, repository) if settings.broker_backend == "saxo" else SimulatedBroker()
    )
    analysis_service = AnalysisService(analyzer, repository)
    trading_service = TradingService(settings, risk_engine, broker, repository)
    position_manager = PositionManager(settings, broker, repository)
    return Container(
        settings=settings,
        database=database,
        repository=repository,
        rate_limiter=rate_limiter,
        analyzer=analyzer,
        risk_engine=risk_engine,
        broker=broker,
        analysis_service=analysis_service,
        trading_service=trading_service,
        position_manager=position_manager,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    configure_logging(resolved.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        container = build_container(resolved)
        app.state.container = container
        logger.info(
            "application_started",
            environment=resolved.environment,
            role=resolved.app_role,
            trading_mode=resolved.trading_mode,
            broker_backend=resolved.broker_backend,
            kill_switch=resolved.trading_kill_switch,
        )
        try:
            yield
        finally:
            await container.rate_limiter.close()
            if isinstance(container.broker, SaxoBroker):
                await container.broker.close()
            container.database.engine.dispose()

    app = FastAPI(
        title="AI-Tader",
        version="0.1.0",
        docs_url=None if resolved.environment == "production" else "/docs",
        redoc_url=None,
        openapi_url=None if resolved.environment == "production" else "/openapi.json",
        lifespan=lifespan,
    )
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=resolved.allowed_host_list,
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware, max_request_bytes=resolved.max_request_bytes)
    if resolved.allowed_origin_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=resolved.allowed_origin_list,
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=[
                "Authorization",
                "Content-Type",
                "Idempotency-Key",
                "X-Admin-Key",
                "X-Live-Confirmation",
                "X-Request-ID",
            ],
        )
    app.include_router(router)

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready", include_in_schema=False)
    async def ready(request: Request) -> JSONResponse:
        container = cast(Container, request.app.state.container)
        database_ok = container.database.ping()
        cache_ok = await container.rate_limiter.healthy()
        code = 200 if database_ok and cache_ok else 503
        return JSONResponse({"status": "ready" if code == 200 else "not_ready"}, status_code=code)

    @app.exception_handler(Exception)
    async def unhandled_exception(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_exception", error_type=type(exc).__name__)
        return JSONResponse(
            {"detail": "internal server error"},
            status_code=500,
        )

    return app


app = create_app()
