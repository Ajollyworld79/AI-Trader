from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from app.workers.runner import Job, Worker


class FakeRepository:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def record_audit(self, **values: Any) -> None:
        self.events.append(values)


def worker_for(role: str) -> Worker:
    worker = Worker.__new__(Worker)
    worker.settings = SimpleNamespace(app_role=role)
    worker.repository = FakeRepository()
    worker.stop = asyncio.Event()
    return worker


@pytest.mark.anyio
async def test_worker_accepts_only_role_authorized_data_jobs() -> None:
    worker = worker_for("market")
    accepted = Job(
        id="1234567890abcdef",
        type="market_scan",
        created_at="2026-01-01T00:00:00Z",
        payload={"symbols": ["MSFT"]},
    )
    await worker._handle(accepted)
    assert worker.repository.events[0]["outcome"] == "accepted"

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
