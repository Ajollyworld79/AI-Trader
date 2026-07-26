from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque

from redis.asyncio import Redis
from redis.exceptions import RedisError


class RateLimiter:
    def __init__(self, redis_url: str | None, limit: int) -> None:
        self.limit = limit
        self.redis = Redis.from_url(redis_url, decode_responses=True) if redis_url else None
        self._local: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        if self.redis is not None:
            await self.redis.close()

    async def healthy(self) -> bool:
        if self.redis is None:
            return True
        try:
            return bool(await self.redis.ping())
        except RedisError:
            return False

    async def allow(self, identity: str) -> bool:
        if self.redis is not None:
            try:
                key = f"rate:{identity}:{int(time.time() // 60)}"
                count = await self.redis.incr(key)
                if count == 1:
                    await self.redis.expire(key, 120)
                return int(count) <= self.limit
            except RedisError:
                # Fail closed in a distributed production setup. A development
                # instance without Redis uses the local limiter below.
                return False
        now = time.monotonic()
        async with self._lock:
            bucket = self._local[identity]
            while bucket and bucket[0] <= now - 60:
                bucket.popleft()
            if len(bucket) >= self.limit:
                return False
            bucket.append(now)
            return True
