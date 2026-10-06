"""M1-BE-18: monthly analysis minutes — GET /usage and the 402 QUOTA_EXCEEDED checks on jobs, retries, uploads and
YouTube imports. Real Postgres (RLS) + S3; FakeTasks."""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from pitchvision_api.db.models import Job, Video
from pitchvision_api.main import create_app
from pitchvision_api.services.quota import minutes_of, period
from pitchvision_api.settings import Settings
from pitchvision_schemas import PipelineConfig

from .conftest import S3, Db
from .test_app import assert_problem
from .test_ingest_api import FakeTasks
from .test_jobs_api import _exec


@pytest.fixture
def tasks() -> FakeTasks:
    return FakeTasks()


@pytest.fixture
async def client(migrated: Db, s3: S3, tasks: FakeTasks) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(env="local", dev_auth=True, database_url=migrated.app_url, storage_endpoint=s3.endpoint,
                        storage_bucket=s3.bucket, storage_access_key=s3.access_key, storage_secret_key=s3.secret_key,
                        storage_region="us-east-1", image_tag="sha-test")  # fmt: skip
    app = create_app(settings)
    app.state.tasks = tasks
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await app.state.engine.dispose()


def h(org: uuid.UUID, plan: str = "free", key: bool = False) -> dict[str, str]:
    out = {"X-Dev-Org": str(org), "X-Dev-Role": "editor", "X-Dev-Plan": plan}
    return out | ({"Idempotency-Key": uuid.uuid4().hex} if key else {})


async def _video(db: Db, org: uuid.UUID, minutes: float | None, *, job: str | None = None, kind: str | None = None,
                 created: datetime | None = None) -> tuple[uuid.UUID, uuid.UUID | None]:  # fmt: skip
    async def add(s: Any) -> tuple[uuid.UUID, uuid.UUID | None]:
        v = Video(org_id=org, created_by=uuid.uuid4(), status="ready", source_type="upload", title="m",
                  duration_ms=None if minutes is None else int(minutes * 60_000))  # fmt: skip
        s.add(v)
        await s.flush()
        if job is None:
            return v.id, None
        j = Job(video_id=v.id, org_id=org, image_tag="t", config=PipelineConfig().model_dump(mode="json"),
                status=job, detail={"kind": kind} if kind else None,
                error={"code": "INTERNAL", "retryable": True, "detail": None} if job == "failed" else None)  # fmt: skip
        if created is not None:
            j.created_at = created
        s.add(j)
        await s.flush()
        return v.id, j.id

    ids: tuple[uuid.UUID, uuid.UUID | None] = await _exec(db, org, add)
    return ids


def test_period_and_minutes() -> None:
    assert period(datetime(2026, 12, 15, tzinfo=UTC)) == (datetime(2026, 12, 1, tzinfo=UTC),
                                                         datetime(2027, 1, 1, tzinfo=UTC))  # fmt: skip
    assert [minutes_of(x) for x in (None, 0, 1, 60_000, 60_001, 5_400_000)] == [0, 0, 1, 1, 2, 90]


async def test_usage_counts_running_and_done_analyses(client: httpx.AsyncClient, migrated: Db) -> None:
    org = uuid.uuid4()
    r = await client.get("/api/v1/usage", headers=h(org))
    u = r.json()
    assert (u["plan"], u["minutes_limit"], u["minutes_used"], u["export_watermark"]) == ("free", 120, 0, True)
    assert u["resets_at"] == period()[1].isoformat().replace("+00:00", "Z")
    await _video(migrated, org, 89.5, job="done")                          # 90
    await _video(migrated, org, 10, job="inferring")                       # 10
    await _video(migrated, org, 30, job="failed")                          # not charged
    await _video(migrated, org, 30, job="cancelled")
    await _video(migrated, org, 30, job="done", kind="recalibrate")        # derived: free
    await _video(migrated, org, 30, job="done", created=period()[0] - timedelta(seconds=1))  # last month
    await _video(migrated, org, None, job="queued")                        # YouTube, duration unknown yet
    await _video(migrated, uuid.uuid4(), 500, job="done")                  # another org
    assert (await client.get("/api/v1/usage", headers=h(org))).json()["minutes_used"] == 100
    pro = (await client.get("/api/v1/usage", headers=h(org, "pro"))).json()
    assert (pro["minutes_limit"], pro["export_max_height"], pro["export_watermark"]) == (900, 1080, False)


async def test_job_upload_import_and_retry_refused_over_quota(client: httpx.AsyncClient, migrated: Db,
                                                               tasks: FakeTasks) -> None:  # fmt: skip
    org = uuid.uuid4()
    await _video(migrated, org, 90, job="done")
    big, _ = await _video(migrated, org, 40)
    p = assert_problem(await client.post("/api/v1/jobs", json={"video_id": str(big)}, headers=h(org, key=True)),
                       402, "QUOTA_EXCEEDED")  # fmt: skip
    assert p["detail"] == "Free plan allows 120 minutes/month; 90 used, video is 40 minutes."
    assert (p["reason"], p["used"], p["limit"], p["needed"]) == ("quota", 90, 120, 40)
    small, _ = await _video(migrated, org, 30)
    r = await client.post("/api/v1/jobs", json={"video_id": str(small)}, headers=h(org, key=True))
    assert r.status_code == 202, r.text  # exactly 120
    r = await client.post("/api/v1/jobs", json={"video_id": str(big)}, headers=h(org, "pro", key=True))
    assert r.status_code == 202  # Pro: 900

    full = uuid.uuid4()
    await _video(migrated, full, 120, job="done")
    body = {"filename": "a.mp4", "size": 10, "mime": "video/mp4"}
    assert_problem(await client.post("/api/v1/videos/uploads", json=body, headers=h(full)), 402, "QUOTA_EXCEEDED")
    yt = {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "rights_confirmed": True}
    assert_problem(await client.post("/api/v1/videos/from-url", json=yt, headers=h(full)), 402, "QUOTA_EXCEEDED")
    assert (await client.post("/api/v1/videos/uploads", json=body, headers=h(full, "pro"))).status_code == 201

    _, failed = await _video(migrated, full, 10, job="failed")
    assert_problem(await client.post(f"/api/v1/jobs/{failed}/retry", headers=h(full)), 402, "QUOTA_EXCEEDED")
    assert (await client.post(f"/api/v1/jobs/{failed}/retry", headers=h(full, "pro"))).status_code == 202
