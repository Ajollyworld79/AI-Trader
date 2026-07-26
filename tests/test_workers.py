from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.domain.entities import AIAnalysis, Decision, FeatureSet
from app.domain.indicators import compute_features
from app.workers.runner import Job, Worker
from tests.conftest import candle_series


class FakeRepository:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def record_audit(self, **values: Any) -> None:
        self.events.append(values)

    def save_candle(self, **values: Any) -> str:
        self.events.append({"kind": "candle", **values})
        return "candle-id"

    def save_news(self, **values: Any) -> str:
        self.events.append({"kind": "news", **values})
        return "news-id"


class FakeRedis:
    def __init__(self) -> None:
        self.pushed: list[tuple[str, str]] = []
        self.incoming: tuple[str, str] | None = None
        self.claimed = True

    async def lpush(self, queue: str, raw: str) -> None:
        self.pushed.append((queue, raw))

    async def brpop(self, _queue: str, **options: Any) -> tuple[str, str] | None:
        assert options["timeout"] == 5
        return self.incoming

    async def set(self, *_args: Any, **_kwargs: Any) -> bool:
        return self.claimed


def worker_for(role: str) -> Worker:
    worker = Worker.__new__(Worker)
    worker.settings = SimpleNamespace(
        app_role=role,
        market_horizon_minutes=60,
        market_candle_count=120,
        trading_universe=(),
        auto_execute_signals=False,
        worker_live_confirmation_token=None,
    )
    worker.repository = FakeRepository()
    worker.redis = FakeRedis()
    worker.container = SimpleNamespace()
    worker.stop = asyncio.Event()
    return worker


@pytest.mark.anyio
async def test_worker_accepts_only_role_authorized_data_jobs() -> None:
    worker = worker_for("market")
    handled: list[str] = []

    async def market_scan(job: Job) -> None:
        handled.append(job.id)

    worker._market_scan = market_scan
    accepted = Job(
        id="1234567890abcdef",
        type="market_scan",
        created_at="2026-01-01T00:00:00Z",
        payload={"symbol": "MSFT"},
    )
    await worker._handle(accepted)
    assert handled == [accepted.id]
    assert worker.repository.events[0]["outcome"] == "completed"

    wrong_role = accepted.model_copy(update={"type": "news_refresh"})
    await worker._handle(wrong_role)
    assert len(worker.repository.events) == 1


@pytest.mark.anyio
async def test_worker_rejects_executable_or_secret_fields() -> None:
    worker = worker_for("strategy")
    for key in ("command", "code", "url", "token", "secret"):
        job = Job(
            id=f"1234567890abcdef{key}",
            type="strategy_evaluation",
            created_at="2026-01-01T00:00:00Z",
            payload={key: "malicious"},
        )
        await worker._handle(job)
    assert worker.repository.events == []


@pytest.mark.anyio
async def test_scheduler_enqueues_market_position_and_news_jobs() -> None:
    worker = worker_for("scheduler")
    worker.settings.trading_universe = (
        {"symbol": "MSFT", "sector": "Technology", "asset_type": "Stock", "uic": 1234},
    )
    worker.stop.set()
    await worker._schedule()
    queues = [queue for queue, _raw in worker.redis.pushed]
    assert queues == ["ai-tader:market", "ai-tader:position", "ai-tader:news"]


@pytest.mark.anyio
async def test_market_and_strategy_pipeline_enqueues_buy_signal() -> None:
    worker = worker_for("market")
    candles = candle_series(70)

    class FakeBroker:
        async def historical_candles(self, **_values: Any) -> list[Any]:
            return candles

    async def analyze(**_values: Any) -> tuple[FeatureSet, AIAnalysis, str]:
        return (
            compute_features(candles),
            AIAnalysis(
                decision=Decision.BUY,
                confidence=0.9,
                reason="approved",
                model_tier="mini",
                provider="azure",
            ),
            "12345678-1234-1234-1234-123456789012",
        )

    worker.container = SimpleNamespace(
        broker=FakeBroker(),
        analysis_service=SimpleNamespace(analyze=analyze),
    )
    market_job = Job(
        id="marketjob12345678",
        type="market_scan",
        created_at="2026-01-01T00:00:00Z",
        payload={
            "symbol": "MSFT",
            "sector": "Technology",
            "asset_type": "Stock",
            "uic": 1234,
        },
    )
    await worker._market_scan(market_job)
    assert len([event for event in worker.repository.events if event["kind"] == "candle"]) == 70
    _queue, raw = worker.redis.pushed[-1]

    worker.settings.app_role = "strategy"
    worker.settings.auto_execute_signals = True
    strategy_job = Job.model_validate_json(raw)
    await worker._strategy_evaluation(strategy_job)
    execution_queue, execution_raw = worker.redis.pushed[-1]
    assert execution_queue == "ai-tader:execution"
    assert json.loads(execution_raw)["payload"]["signal_id"].endswith("9012")


@pytest.mark.anyio
async def test_consumer_handles_news_and_ignores_duplicate_jobs() -> None:
    worker = worker_for("news")
    job = Job(
        id="newsjob123456789",
        type="news_refresh",
        created_at="2026-01-01T00:00:00Z",
        payload={
            "items": [
                {
                    "external_id": "news-1",
                    "symbol": "MSFT",
                    "headline": "Headline",
                    "source": "test",
                    "published_at": "2026-01-01T00:00:00Z",
                    "sentiment": "positive",
                    "payload": {},
                }
            ]
        },
    )
    worker.redis.incoming = ("ai-tader:news", job.model_dump_json())
    await worker._consume()
    assert any(event.get("kind") == "news" for event in worker.repository.events)

    before = len(worker.repository.events)
    worker.redis.claimed = False
    await worker._consume()
    assert len(worker.repository.events) == before


@pytest.mark.anyio
async def test_execution_and_position_handlers_call_their_services() -> None:
    worker = worker_for("execution")
    worker.settings.auto_execute_signals = True
    calls: list[str] = []

    async def execute_signal(**values: Any) -> None:
        calls.append(values["signal_id"])

    async def reconcile() -> dict[str, int]:
        calls.append("reconcile")
        return {"checked": 0}

    worker.container = SimpleNamespace(
        trading_service=SimpleNamespace(execute_signal=execute_signal),
        position_manager=SimpleNamespace(reconcile=reconcile),
    )
    execution = Job(
        id="execution12345678",
        type="execution_review",
        created_at="2026-01-01T00:00:00Z",
        payload={"signal_id": "12345678-1234-1234-1234-123456789012"},
    )
    await worker._handle(execution)
    worker.settings.app_role = "position"
    await worker._handle(
        Job(
            id="position123456789",
            type="position_reconciliation",
            created_at="2026-01-01T00:00:00Z",
            payload={},
        )
    )
    assert calls == ["12345678-1234-1234-1234-123456789012", "reconcile"]


def test_worker_payload_helpers_fail_closed() -> None:
    with pytest.raises(ValueError):
        Worker._required_str({}, "symbol")
    with pytest.raises(ValueError):
        Worker._required_int({"uic": True}, "uic")
