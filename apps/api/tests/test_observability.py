"""M1-OPS-08: GET /metrics (token-gated Prometheus text) and the HTTP / queue / ingest / job series."""

import uuid

import redis

from .conftest import Db
from .test_app import build, client_for
from .test_jobs_api import _video

TOKEN = "metrics-secret-token"


async def test_metrics_gate() -> None:
    async for c in client_for(build(env="staging", tile_signing_key="k" * 32)):
        assert (await c.get("/metrics")).status_code == 404  # no token configured: hidden outside local
    async for c in client_for(build(env="staging", tile_signing_key="k" * 32, metrics_token=TOKEN)):
        assert (await c.get("/metrics")).status_code == 404
        assert (await c.get("/metrics", headers={"Authorization": "Bearer nope"})).status_code == 404


async def test_metrics_series(migrated: Db, redis_url: str) -> None:
    r = redis.Redis.from_url(redis_url)
    r.delete("cpu", "pv:metrics:ingest_total")
    r.rpush("cpu", "t1", "t2")
    r.hincrby("pv:metrics:ingest_total", "download:success:-", 7)
    r.hincrby("pv:metrics:ingest_total", "download:failed:bot_check", 2)
    await _video(migrated, uuid.uuid4())
    async for c in client_for(build(database_url=migrated.app_url, redis_url=redis_url, metrics_token=TOKEN)):
        await c.get("/api/v1/meta")
        await c.get(f"/api/v1/videos/{uuid.uuid4()}", headers={"X-Dev-Org": str(uuid.uuid4())})
        res = await c.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})
        assert res.status_code == 200 and res.headers["content-type"].startswith("text/plain")
        body = res.text
    r.delete("cpu", "pv:metrics:ingest_total")
    assert 'pv_queue_depth{queue="cpu"} 2.0' in body
    assert 'pv_ingest_total{op="download",outcome="failed",reason="bot_check"} 2.0' in body
    assert 'pv_dependency_up{dependency="redis"} 1.0' in body
    assert 'pv_dependency_up{dependency="postgres"} 1.0' in body
    assert 'pv_http_requests_total{method="GET",route="/api/v1/meta",status="200"} 1.0' in body
    assert 'route="/api/v1/videos/{video_id}"' in body  # templates, never raw ids
    assert 'route="/metrics"' not in body


async def test_metrics_report_down_dependencies() -> None:
    async for c in client_for(build(redis_url="redis://127.0.0.1:1/0", metrics_token=TOKEN)):
        body = (await c.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})).text
    assert 'pv_dependency_up{dependency="redis"} 0.0' in body
    assert 'pv_dependency_up{dependency="postgres"} 0.0' in body


def test_init_sentry_only_with_a_dsn() -> None:
    import sentry_sdk
    from pitchvision_api.observability import init_sentry
    from pitchvision_api.settings import Settings

    assert init_sentry(Settings()) is False
    try:
        assert init_sentry(Settings(sentry_dsn="https://k@o0.ingest.sentry.io/0", image_tag="sha-1")) is True
        client = sentry_sdk.get_client()
        assert client.options["release"] == "sha-1" and client.options["send_default_pii"] is False
    finally:
        sentry_sdk.init()  # back to a disabled client
