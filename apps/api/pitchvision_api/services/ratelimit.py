"""Per-user rate limits on the expensive endpoints (AD §14 "lạm dụng tài nguyên GPU", M1-BE-22).

Redis token bucket, one Lua call per request (atomic across API replicas): a bucket holds ``burst`` tokens and
refills at ``per_hour`` tokens an hour. Plan quotas (minutes / month) stay the real cost ceiling; this only
stops floods — scripted job spam, yt-dlp probe hammering (gets our egress IP bot-checked, SP-10), export loops.

Fails open: if Redis is unreachable the request goes through (the broker is the same Redis, so the endpoint
itself will report the outage). Keys: ``pv:rl:{rule}:{user_id}``, expiring once the bucket would be full again.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any, Protocol

from fastapi import Depends, Request
from pitchvision_schemas import ErrorCode

from pitchvision_api.auth import Principal, current_principal
from pitchvision_api.errors import ApiError

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Rule:
    name: str
    burst: int
    per_hour: float

    @property
    def rate_per_s(self) -> float:
        return self.per_hour / 3600


# Sized for a real user's busiest hour with room to spare; a script hits them within minutes.
INGEST_PREVIEW = Rule("ingest_preview", burst=30, per_hour=120)  # every pasted URL is a yt-dlp metadata call
INGEST = Rule("ingest", burst=10, per_hour=30)
UPLOADS = Rule("uploads", burst=20, per_hour=60)  # creating upload sessions; signing parts is not limited
JOBS = Rule("jobs", burst=20, per_hour=60)  # analyses + derived jobs (retry, team rerun, recalibrate, repitch)
EXPORTS = Rule("exports", burst=10, per_hour=30)
DATA_EXPORTS = Rule("data_exports", burst=3, per_hour=3 / 24)  # full account zip: 3, then one every 8 h

# KEYS[1] bucket · ARGV burst, rate/s, now (s) → {allowed 0/1, retry_after ms}
_TOKEN_BUCKET = """
local burst = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local b = redis.call('HMGET', KEYS[1], 't', 'ts')
local tokens = tonumber(b[1]) or burst
local ts = tonumber(b[2]) or now
tokens = math.min(burst, tokens + math.max(0, now - ts) * rate)
local allowed, retry_ms = 0, math.ceil((1 - tokens) / rate * 1000)
if tokens >= 1 then
  tokens, allowed, retry_ms = tokens - 1, 1, 0
end
redis.call('HSET', KEYS[1], 't', tokens, 'ts', now)
redis.call('PEXPIRE', KEYS[1], math.ceil((burst - tokens) / rate * 1000) + 1000)
return {allowed, retry_ms}
"""


class RateLimiter(Protocol):
    async def take(self, rule: Rule, subject: str) -> float | None:
        """Consume one token; None when allowed, else seconds until the next one."""


class RedisRateLimiter:
    def __init__(self, redis: Any, clock: Callable[[], float] = time.time) -> None:
        self._script = redis.register_script(_TOKEN_BUCKET)
        self._clock = clock

    async def take(self, rule: Rule, subject: str) -> float | None:
        try:
            allowed, retry_ms = await self._script(
                keys=[f"pv:rl:{rule.name}:{subject}"], args=[rule.burst, rule.rate_per_s, self._clock()]
            )
        except Exception as e:
            log.warning("rate limit %s: redis unavailable (%r); allowing", rule.name, e)
            return None
        return None if int(allowed) else max(int(retry_ms), 1) / 1000


class NoRateLimiter:
    async def take(self, rule: Rule, subject: str) -> float | None:
        return None


def rate_limited(rule: Rule) -> Callable[..., Awaitable[None]]:
    """Endpoint dependency: ``dependencies=[Depends(rate_limited(JOBS))]``. 429 RATE_LIMITED + Retry-After."""

    async def dep(request: Request, principal: Annotated[Principal, Depends(current_principal)]) -> None:
        limiter: RateLimiter = request.app.state.ratelimiter
        retry_after = await limiter.take(rule, str(principal.user_id))
        if retry_after is None:
            return
        request.app.state.metrics.rate_limited(rule.name)
        seconds = math.ceil(retry_after)
        raise ApiError(
            ErrorCode.RATE_LIMITED, 429, "Too many requests",
            f"Slow down: try again in {seconds} s.",
            headers={"Retry-After": str(seconds)},  # the web client exposes it as problem.retry_after
        )  # fmt: skip

    return dep
