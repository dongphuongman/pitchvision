"""Sliding-window rate limiter behind a small port (in-memory now; a Redis adapter can replace it later).

Per-process only: with several uvicorn workers/replicas each keeps its own counters, so the effective
limit is N x the configured one until the Redis adapter lands.
"""

import asyncio
import time
from collections import deque
from dataclasses import dataclass
from typing import Protocol

from pitchvision_schemas import ErrorCode

from pitchvision_identity.errors import ApiError


@dataclass(frozen=True)
class Rule:
    name: str
    limit: int
    window_s: float


# UIUX SCR-02: 5 failed sign-ins -> locked 15 min. SCR-04/06: resend is locked 60 s client-side.
LOGIN_FAILURES = Rule("login", 5, 15 * 60)
FORGOT = Rule("forgot", 5, 15 * 60)
RESEND = Rule("resend", 3, 10 * 60)


class RateLimiter(Protocol):
    async def retry_after(self, rule: Rule, key: str) -> float | None:
        """Seconds until the next hit is allowed, or None if it is allowed now."""

    async def hit(self, rule: Rule, key: str) -> None: ...

    async def reset(self, rule: Rule, key: str) -> None: ...


class MemoryRateLimiter:
    def __init__(self, clock: "Clock | None" = None) -> None:
        self._hits: dict[tuple[str, str], deque[float]] = {}
        self._lock = asyncio.Lock()
        self._clock = clock or time.monotonic

    def _window(self, rule: Rule, key: str, now: float) -> deque[float]:
        q = self._hits.setdefault((rule.name, key), deque())
        while q and q[0] <= now - rule.window_s:
            q.popleft()
        return q

    async def retry_after(self, rule: Rule, key: str) -> float | None:
        async with self._lock:
            now = self._clock()
            q = self._window(rule, key, now)
            return q[0] + rule.window_s - now if len(q) >= rule.limit else None

    async def hit(self, rule: Rule, key: str) -> None:
        async with self._lock:
            now = self._clock()
            self._window(rule, key, now).append(now)
            if len(self._hits) > 100_000:  # crude bound on memory: drop empty windows
                for k in [k for k, v in self._hits.items() if not v]:
                    del self._hits[k]

    async def reset(self, rule: Rule, key: str) -> None:
        async with self._lock:
            self._hits.pop((rule.name, key), None)


class Clock(Protocol):
    def __call__(self) -> float: ...


async def enforce(limiter: RateLimiter, rule: Rule, key: str, *, count: bool = True) -> None:
    """Raise 429 RATE_LIMITED (with Retry-After) when over the limit; otherwise record the hit if `count`."""
    wait = await limiter.retry_after(rule, key)
    if wait is not None:
        seconds = max(1, int(wait + 0.999))
        raise ApiError(
            ErrorCode.RATE_LIMITED,
            429,
            "Too many attempts",
            f"Try again in {seconds} s.",
            headers={"Retry-After": str(seconds)},
        )
    if count:
        await limiter.hit(rule, key)
