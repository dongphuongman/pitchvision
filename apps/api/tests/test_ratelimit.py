"""M1-BE-22: per-user Redis token buckets on the expensive endpoints (services/ratelimit.py)."""

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
import redis.asyncio as aioredis
from pitchvision_api.main import create_app
from pitchvision_api.services.ratelimit import INGEST_PREVIEW, NoRateLimiter, RedisRateLimiter, Rule
from pitchvision_api.settings import Settings

from .test_app import DEAD_DB, assert_problem


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


async def test_bucket_allows_a_burst_then_refills(redis_url: str) -> None:
    redis, clock = aioredis.Redis.from_url(redis_url), Clock()
    limiter = RedisRateLimiter(redis, clock)
    rule = Rule(f"t{uuid.uuid4().hex[:8]}", burst=3, per_hour=3600)  # one token a second
    try:
        assert [await limiter.take(rule, "u1") for _ in range(3)] == [None, None, None]
        assert await limiter.take(rule, "u1") == pytest.approx(1.0, abs=0.01)
        assert await limiter.take(rule, "u2") is None  # buckets are per subject
        clock.now += 0.5
        assert await limiter.take(rule, "u1") == pytest.approx(0.5, abs=0.01)
        clock.now += 0.5
        assert await limiter.take(rule, "u1") is None
        clock.now += 3600  # refills to the burst, never beyond it
        assert [await limiter.take(rule, "u1") for _ in range(4)][-1] is not None
        ttl = await redis.pttl(f"pv:rl:{rule.name}:u1")
        assert 0 < ttl <= 4000  # expires once it would be full again (+1 s)
    finally:
        await redis.aclose()


async def test_redis_down_fails_open() -> None:
    redis = aioredis.Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.5)
    try:
        assert await RedisRateLimiter(redis).take(INGEST_PREVIEW, "u") is None
    finally:
        await redis.aclose()


class Recorder:
    def __init__(self, retry_after: float | None) -> None:
        self.retry_after, self.calls = retry_after, list[tuple[str, str]]()

    async def take(self, rule: Rule, subject: str) -> float | None:
        self.calls.append((rule.name, subject))
        return self.retry_after


@pytest.fixture
async def limited() -> AsyncIterator[tuple[httpx.AsyncClient, Recorder]]:
    app = create_app(Settings(env="local", dev_auth=True, database_url=DEAD_DB, rate_limits=True))
    assert isinstance(app.state.ratelimiter, RedisRateLimiter)
    app.state.ratelimiter = rec = Recorder(12.3)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, rec


async def test_over_the_limit_is_429_with_retry_after(limited: tuple[httpx.AsyncClient, Recorder]) -> None:
    client, rec = limited
    user = uuid.uuid4()
    headers = {"X-Dev-Org": str(uuid.uuid4()), "X-Dev-User": str(user), "X-Dev-Role": "editor"}
    r = await client.post("/api/v1/videos/from-url/preview", json={"url": "youtu.be/dQw4w9WgXcQ"}, headers=headers)
    body = assert_problem(r, 429, "RATE_LIMITED")
    assert r.headers["retry-after"] == "13" and "13 s" in str(body["detail"])
    assert rec.calls == [("ingest_preview", str(user))]
    metrics = (await client.get("/metrics")).text
    assert 'pv_rate_limited_total{rule="ingest_preview"} 1.0' in metrics


async def test_unauthenticated_requests_are_rejected_before_the_limiter(
    limited: tuple[httpx.AsyncClient, Recorder],
) -> None:
    client, rec = limited
    r = await client.post("/api/v1/jobs", json={})
    assert r.status_code == 401 and rec.calls == []


def test_limits_off_uses_no_limiter() -> None:
    app = create_app(Settings(database_url=DEAD_DB, rate_limits=False))
    assert isinstance(app.state.ratelimiter, NoRateLimiter)


X = "00000000-0000-4000-8000-0000000000aa"
LIMITED = {
    "/api/v1/videos/from-url/preview": "ingest_preview",
    "/api/v1/videos/from-url": "ingest",
    "/api/v1/videos/uploads": "uploads",
    "/api/v1/jobs": "jobs",
    f"/api/v1/jobs/{X}/retry": "jobs",
    f"/api/v1/results/{X}/teams/manual": "jobs",
    f"/api/v1/results/{X}/calibration": "jobs",
    f"/api/v1/results/{X}/repitch": "jobs",
    "/api/v1/exports": "exports",
    f"/api/v1/exports/{X}/retry": "exports",
    "/api/v1/privacy/data-exports": "data_exports",
}


async def test_expensive_endpoints_are_limited(limited: tuple[httpx.AsyncClient, Recorder]) -> None:
    client, rec = limited
    headers = {"X-Dev-Org": str(uuid.uuid4()), "X-Dev-Role": "owner"}
    for path, rule in LIMITED.items():
        rec.calls.clear()
        r = await client.post(path, json={}, headers=headers)
        assert r.status_code == 429, path
        assert [name for name, _ in rec.calls] == [rule], path
    rec.calls.clear()  # cheap or self-limiting calls stay unlimited (signing parts, cancelling, marking read)
    for path in (f"/api/v1/videos/{X}/uploads/sign", f"/api/v1/jobs/{X}/cancel", "/api/v1/notifications/read"):
        r = await client.post(path, json={}, headers=headers)
        assert r.status_code != 429, path
    assert rec.calls == []
