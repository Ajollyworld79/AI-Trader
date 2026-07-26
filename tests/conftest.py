from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.config import Settings
from app.domain.entities import Candle
from app.security import hash_secret

API_KEY = "ait_test_api_key_with_sufficient_entropy_123"
ADMIN_KEY = "ait_test_admin_key_with_sufficient_entropy_456"
LIVE_KEY = "ait_test_live_key_with_sufficient_entropy_789"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="test",
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        api_key_hashes=hash_secret(API_KEY),
        admin_key_hashes=hash_secret(ADMIN_KEY),
        allowed_hosts="testserver,localhost",
        trading_kill_switch=True,
    )


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {API_KEY}"}


@pytest.fixture
def admin_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {API_KEY}",
        "X-Admin-Key": ADMIN_KEY,
    }


def candle_series(count: int = 120, start: float = 100.0) -> list[Candle]:
    base = datetime(2025, 1, 1, tzinfo=UTC)
    result: list[Candle] = []
    for index in range(count):
        close = start + index * 0.4 + ((index % 7) - 3) * 0.08
        opening = close - 0.15
        result.append(
            Candle(
                timestamp=base + timedelta(days=index),
                open=opening,
                high=max(opening, close) + 0.6,
                low=min(opening, close) - 0.6,
                close=close,
                volume=10_000 + index * 10,
            )
        )
    return result


def candle_payload(count: int = 120) -> list[dict[str, Any]]:
    return [
        {
            "timestamp": item.timestamp.isoformat(),
            "open": item.open,
            "high": item.high,
            "low": item.low,
            "close": item.close,
            "volume": item.volume,
            "dividend": item.dividend,
            "split_ratio": item.split_ratio,
        }
        for item in candle_series(count)
    ]
