from __future__ import annotations

import asyncio
import signal
import uuid
from contextlib import suppress
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.broker.saxo import SaxoBroker
from app.config import get_settings
from app.domain.entities import Candle, Decision, OrderType
from app.domain.market import classify_market_regime
from app.logging import configure_logging
from app.main import build_container

logger = structlog.get_logger()


class Job(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=16, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    type: Literal[
        "market_scan",
        "benchmark_scan",
        "strategy_evaluation",
        "execution_review",
        "position_reconciliation",
        "news_refresh",
    ]
    created_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class Worker:
    def __init__(self) -> None:
        self.settings = get_settings()
        if self.settings.app_role == "api":
            raise RuntimeError("APP_ROLE must select a worker role")
        if not self.settings.redis_url:
            raise RuntimeError("workers require REDIS_URL")
        self.redis = Redis.from_url(self.settings.redis_url, decode_responses=True)
        self.container = build_container(self.settings)
        self.repository = self.container.repository
        self.stop = asyncio.Event()

    async def run(self) -> None:
        logger.info("worker_started", role=self.settings.app_role)
        while not self.stop.is_set():
            try:
                if self.settings.app_role == "scheduler":
                    await self._schedule()
                else:
                    await self._consume()
            except RedisError as exc:
                logger.warning("worker_redis_error", error_type=type(exc).__name__)
                await asyncio.sleep(5)
            except Exception as exc:
                logger.exception("worker_iteration_failed", error_type=type(exc).__name__)
                await asyncio.sleep(5)

    async def close(self) -> None:
        self.stop.set()
        await self.redis.close()
        await self.container.rate_limiter.close()
        if isinstance(self.container.broker, SaxoBroker):
            await self.container.broker.close()
        if self.container.market_intelligence:
            await self.container.market_intelligence.close()
        self.container.database.engine.dispose()

    async def _schedule(self) -> None:
        now = datetime.now(UTC)
        market_scheduled = await self._claim_schedule(
            "market", self.settings.market_scan_interval_seconds, now
        )
        if market_scheduled:
            benchmark = self.settings.market_benchmark
            if benchmark:
                await self._enqueue("market", "benchmark_scan", dict(benchmark), now)
            for instrument in self.settings.trading_universe:
                await self._enqueue("market", "market_scan", dict(instrument), now)
        position_scheduled = await self._claim_schedule("position", 60, now)
        if position_scheduled:
            await self._enqueue("position", "position_reconciliation", {}, now)
        news_scheduled = False
        if self.settings.alpha_vantage_api_key:
            news_scheduled = await self._claim_schedule(
                "news", self.settings.news_refresh_seconds, now
            )
            if news_scheduled:
                await self._enqueue("news", "news_refresh", {}, now)
        logger.info(
            "jobs_scheduled",
            instruments=len(self.settings.trading_universe) if market_scheduled else 0,
            market=market_scheduled,
            position_reconciliation=position_scheduled,
            news=news_scheduled,
        )
        with suppress(TimeoutError):
            await asyncio.wait_for(self.stop.wait(), timeout=60)

    async def _enqueue(
        self,
        role: str,
        job_type: Literal[
            "market_scan",
            "benchmark_scan",
            "strategy_evaluation",
            "execution_review",
            "position_reconciliation",
            "news_refresh",
        ],
        payload: dict[str, Any],
        created_at: datetime | None = None,
    ) -> None:
        job = Job(
            id=uuid.uuid4().hex,
            type=job_type,
            created_at=created_at or datetime.now(UTC),
            payload=payload,
        )
        await self.redis.lpush(f"ai-tader:{role}", job.model_dump_json())

    async def _claim_schedule(self, name: str, interval: int, now: datetime) -> bool:
        bucket = int(now.timestamp()) // interval
        return bool(
            await self.redis.set(
                f"ai-tader:schedule:{name}:{bucket}",
                "1",
                ex=interval * 2,
                nx=True,
            )
        )

    async def _consume(self) -> None:
        result = await self.redis.brpop(f"ai-tader:{self.settings.app_role}", timeout=5)
        if not result:
            return
        _queue, raw = result
        if len(raw) > 64_000:
            logger.warning("job_rejected", reason="payload_too_large")
            return
        try:
            job = Job.model_validate_json(raw)
        except ValidationError:
            logger.warning("job_rejected", reason="invalid_schema")
            return
        claimed = await self.redis.set(f"ai-tader:job:{job.id}", "1", ex=86_400, nx=True)
        if not claimed:
            logger.info("job_duplicate_ignored", job_id=job.id)
            return
        try:
            await self._handle(job)
        except Exception as exc:
            await self.redis.lpush(
                "ai-tader:dead-letter",
                Job(
                    id=job.id,
                    type=job.type,
                    created_at=job.created_at,
                    payload={"error_type": type(exc).__name__},
                ).model_dump_json(),
            )
            raise

    async def _handle(self, job: Job) -> None:
        allowed: dict[str, set[str]] = {
            "market": {"market_scan", "benchmark_scan"},
            "strategy": {"strategy_evaluation"},
            "execution": {"execution_review"},
            "position": {"position_reconciliation"},
            "news": {"news_refresh"},
        }
        if job.type not in allowed.get(self.settings.app_role, set()):
            logger.warning(
                "job_rejected",
                job_id=job.id,
                reason="role_not_authorized",
                job_type=job.type,
            )
            return
        forbidden = {"command", "code", "url", "authorization", "token", "secret"}
        if forbidden.intersection(key.lower() for key in job.payload):
            logger.warning("job_rejected", job_id=job.id, reason="forbidden_field")
            return
        if job.type == "market_scan":
            await self._market_scan(job)
        elif job.type == "benchmark_scan":
            await self._benchmark_scan(job)
        elif job.type == "strategy_evaluation":
            await self._strategy_evaluation(job)
        elif job.type == "execution_review":
            await self._execution_review(job)
        elif job.type == "position_reconciliation":
            await self.container.position_manager.reconcile()
        elif job.type == "news_refresh":
            await self._news_refresh(job)
        self.repository.record_audit(
            event_type=f"worker_{job.type}",
            actor_hash="internal-worker",
            correlation_id=job.id,
            outcome="completed",
            details={"role": self.settings.app_role},
        )
        logger.info("job_completed", job_id=job.id, job_type=job.type)

    async def _market_scan(self, job: Job) -> None:
        symbol = self._required_str(job.payload, "symbol")
        sector = self._required_str(job.payload, "sector")
        asset_type = self._required_str(job.payload, "asset_type")
        uic = self._required_int(job.payload, "uic")
        candles = await self.container.broker.historical_candles(
            uic=uic,
            asset_type=asset_type,
            horizon_minutes=self.settings.market_horizon_minutes,
            count=self.settings.market_candle_count,
        )
        if len(candles) < 62:
            raise ValueError(f"insufficient candles for {symbol}")
        for candle in candles:
            values = asdict(candle)
            self.repository.save_candle(
                symbol=symbol,
                timeframe=f"{self.settings.market_horizon_minutes}m",
                **values,
            )
        await self._enqueue(
            "strategy",
            "strategy_evaluation",
            {
                "symbol": symbol,
                "sector": sector,
                "asset_type": asset_type,
                "uic": uic,
                "candles": [
                    {**asdict(candle), "timestamp": candle.timestamp.isoformat()}
                    for candle in candles
                ],
            },
        )

    async def _benchmark_scan(self, job: Job) -> None:
        symbol = self._required_str(job.payload, "symbol")
        asset_type = self._required_str(job.payload, "asset_type")
        uic = self._required_int(job.payload, "uic")
        candles = await self.container.broker.historical_candles(
            uic=uic,
            asset_type=asset_type,
            horizon_minutes=self.settings.market_horizon_minutes,
            count=self.settings.market_candle_count,
        )
        if len(candles) < 62:
            raise ValueError(f"insufficient benchmark candles for {symbol}")
        timeframe = f"{self.settings.market_horizon_minutes}m"
        for candle in candles:
            self.repository.save_candle(
                symbol=symbol,
                timeframe=timeframe,
                **asdict(candle),
            )
        regime = classify_market_regime(
            candles,
            extreme_volatility=self.settings.market_extreme_volatility,
        )
        self.repository.save_market_regime(
            benchmark_symbol=symbol,
            as_of=candles[-1].timestamp,
            regime=regime.regime,
            close=regime.close,
            volatility=regime.volatility,
            adx=regime.adx,
            data_quality_ok=regime.data_quality_ok,
            payload=regime.to_payload(),
        )

    async def _strategy_evaluation(self, job: Job) -> None:
        raw_candles = job.payload.get("candles")
        if not isinstance(raw_candles, list):
            raise ValueError("strategy job is missing candles")
        candles: list[Candle] = []
        for raw in raw_candles:
            if not isinstance(raw, dict):
                raise ValueError("invalid candle payload")
            values = dict(raw)
            raw_timestamp = values.get("timestamp")
            if not isinstance(raw_timestamp, str):
                raise ValueError("invalid candle timestamp")
            values["timestamp"] = datetime.fromisoformat(raw_timestamp)
            candles.append(Candle(**values))
        benchmark = self.settings.market_benchmark
        benchmark_closes = (
            self.repository.latest_candle_closes(
                str(benchmark["symbol"]),
                f"{self.settings.market_horizon_minutes}m",
                limit=self.settings.market_candle_count,
            )
            if benchmark
            else []
        )
        market_context = self.repository.latest_market_regime()
        if market_context:
            regime_as_of = market_context["as_of"]
            age = (datetime.now(UTC) - regime_as_of).total_seconds()
            market_context["data_quality_ok"] = bool(
                market_context["data_quality_ok"]
                and 0 <= age <= self.settings.max_market_regime_age_seconds
            )
            market_context["as_of"] = regime_as_of.isoformat()
        symbol = self._required_str(job.payload, "symbol")
        features, analysis, signal_id = await self.container.analysis_service.analyze(
            symbol=symbol,
            candles=candles,
            benchmark_closes=benchmark_closes if len(benchmark_closes) >= 21 else None,
            news=self.repository.recent_news_context(
                symbol,
                max_age_hours=self.settings.news_max_age_hours,
            ),
            sector=self._required_str(job.payload, "sector"),
            uic=self._required_int(job.payload, "uic"),
            asset_type=self._required_str(job.payload, "asset_type"),
            days_to_earnings=self.repository.days_to_next_earnings(symbol),
            market_context=market_context,
        )
        del features
        if analysis.decision is Decision.BUY and self.settings.auto_execute_signals:
            await self._enqueue(
                "execution",
                "execution_review",
                {"signal_id": signal_id},
            )

    async def _execution_review(self, job: Job) -> None:
        if not self.settings.auto_execute_signals:
            raise ValueError("automatic execution is disabled")
        signal_id = self._required_str(job.payload, "signal_id")
        live_confirmation = (
            self.settings.worker_live_confirmation_token.get_secret_value()
            if self.settings.worker_live_confirmation_token
            else None
        )
        await self.container.trading_service.execute_signal(
            signal_id=signal_id,
            requested_quantity=None,
            order_type=OrderType.MARKET,
            idempotency_key=job.id,
            actor_key="internal-worker",
            correlation_id=job.id,
            live_confirmation=live_confirmation,
        )

    async def _news_refresh(self, job: Job) -> None:
        del job
        provider = self.container.market_intelligence
        if provider is None:
            logger.info("news_refresh_skipped", reason="provider_not_configured")
            return
        symbols = {str(item["symbol"]) for item in self.settings.trading_universe}
        news, earnings = await asyncio.gather(
            provider.fetch_news(
                symbols=symbols,
                lookback_hours=self.settings.news_lookback_hours,
                limit=self.settings.news_max_articles,
            ),
            provider.fetch_earnings(symbols=symbols),
        )
        new_news = sum(bool(self.repository.save_news(**asdict(item))) for item in news)
        new_earnings = sum(bool(self.repository.save_earnings(**asdict(item))) for item in earnings)
        logger.info(
            "market_intelligence_refreshed",
            news_received=len(news),
            news_saved=new_news,
            earnings_received=len(earnings),
            earnings_saved=new_earnings,
        )

    @staticmethod
    def _required_str(payload: dict[str, Any], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"missing {key}")
        return value

    @staticmethod
    def _required_int(payload: dict[str, Any], key: str) -> int:
        value = payload.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"missing {key}")
        return value


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    worker = Worker()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, worker.stop.set)
    try:
        await worker.run()
    finally:
        await worker.close()


if __name__ == "__main__":
    asyncio.run(main())
