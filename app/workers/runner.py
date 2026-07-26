from __future__ import annotations

import asyncio
import signal
import uuid
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.config import get_settings
from app.infrastructure.database import Database
from app.infrastructure.repositories import Repository
from app.logging import configure_logging

logger = structlog.get_logger()


class Job(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=16, max_length=64)
    type: Literal[
        "market_scan",
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
        self.database = Database(self.settings)
        self.repository = Repository(self.database)
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

    async def close(self) -> None:
        self.stop.set()
        await self.redis.close()
        self.database.engine.dispose()

    async def _schedule(self) -> None:
        job = Job(
            id=uuid.uuid4().hex,
            type="market_scan",
            created_at=datetime.now(UTC),
            payload={"source": "scheduler"},
        )
        await self.redis.lpush("ai-tader:market", job.model_dump_json())
        logger.info("job_scheduled", job_id=job.id, job_type=job.type)
        with suppress(TimeoutError):
            await asyncio.wait_for(self.stop.wait(), timeout=60)

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
        await self._handle(job)

    async def _handle(self, job: Job) -> None:
        allowed: dict[str, set[str]] = {
            "market": {"market_scan"},
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
        # Jobs are intentionally data-only. They cannot carry Python, URLs, shell
        # commands, credentials or executable tool definitions.
        forbidden = {"command", "code", "url", "authorization", "token", "secret"}
        if forbidden.intersection(key.lower() for key in job.payload):
            logger.warning("job_rejected", job_id=job.id, reason="forbidden_field")
            return
        self.repository.record_audit(
            event_type=f"worker_{job.type}",
            actor_hash="internal-worker",
            correlation_id=job.id,
            outcome="accepted",
            details={"role": self.settings.app_role},
        )
        logger.info("job_completed", job_id=job.id, job_type=job.type)


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
