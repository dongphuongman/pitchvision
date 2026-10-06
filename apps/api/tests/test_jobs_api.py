"""M1-BE-09: POST /jobs (idempotent, one active job per video), get/list, cancel, retry — real Postgres + RLS."""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from pitchvision_api.db import make_engine, make_sessionmaker, org_session
from pitchvision_api.db.models import Job, JobTask, Video
from pitchvision_api.main import create_app
from pitchvision_api.settings import Settings
from pitchvision_schemas import PipelineConfig
from sqlalchemy import select, update

from .conftest import Db
from .test_app import assert_problem
from .test_ingest_api import FakeTasks, _client, who


@pytest.fixture
def tasks() -> FakeTasks:
    return FakeTasks()


@pytest.fixture
async def client(migrated: Db, tasks: FakeTasks) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(Settings(env="local", dev_auth=True, database_url=migrated.app_url, image_tag="sha-test"))
    app.state.tasks = tasks
    async for c in _client(app):
        yield c


async def _exec(db: Db, org: uuid.UUID, fn: Any) -> Any:
    engine = make_engine(db.app_url)
    try:
        async with org_session(make_sessionmaker(engine), org) as s:
            return await fn(s)
    finally:
        await engine.dispose()


async def _video(db: Db, org: uuid.UUID, status: str = "ready", source_type: str = "upload") -> uuid.UUID:
    async def add(s: Any) -> uuid.UUID:
        v = Video(org_id=org, created_by=uuid.uuid4(), status=status, source_type=source_type, title="match")
        s.add(v)
        await s.flush()
        return v.id

    vid: uuid.UUID = await _exec(db, org, add)
    return vid


def idem(org: uuid.UUID, role: str = "editor", key: str | None = None) -> dict[str, str]:
    return who(org, role) | {"Idempotency-Key": key or uuid.uuid4().hex}


async def _create(client: httpx.AsyncClient, org: uuid.UUID, vid: uuid.UUID, **body: Any) -> dict[str, Any]:
    r = await client.post("/api/v1/jobs", json={"video_id": str(vid), **body}, headers=idem(org))
    assert r.status_code == 202, r.text
    data: dict[str, Any] = r.json()
    return data


async def test_create_job_happy_path(client: httpx.AsyncClient, tasks: FakeTasks, migrated: Db) -> None:
    org = uuid.uuid4()
    vid = await _video(migrated, org)
    job = await _create(client, org, vid, config={"frame_stride": 2})
    assert job["status"] == "queued" and job["video_id"] == str(vid) and job["progress"] == 0
    assert job["image_tag"] == "sha-test" and job["error"] is None and job["coverage"] is None
    assert job["config"] == PipelineConfig(frame_stride=2).model_dump(mode="json")
    assert tasks.sent == [("orchestrate.start_job", {"job_id": job["id"], "org_id": str(org)}, "orchestrate")]
    got = await client.get(f"/api/v1/jobs/{job['id']}", headers=who(org, "viewer"))
    assert got.status_code == 200 and got.json()["id"] == job["id"]
    listed = await client.get(f"/api/v1/videos/{vid}/jobs", headers=who(org, "viewer"))
    assert [j["id"] for j in listed.json()["items"]] == [job["id"]]
    # another org sees nothing
    assert_problem(await client.get(f"/api/v1/jobs/{job['id']}", headers=who(uuid.uuid4())), 404, "NOT_FOUND")


async def test_idempotent_replay(client: httpx.AsyncClient, tasks: FakeTasks, migrated: Db) -> None:
    org = uuid.uuid4()
    vid = await _video(migrated, org)
    headers = idem(org, key="create-1")
    first = await client.post("/api/v1/jobs", json={"video_id": str(vid)}, headers=headers)
    again = await client.post("/api/v1/jobs", json={"video_id": str(vid)}, headers=headers)
    assert first.status_code == again.status_code == 202
    assert again.json() == first.json() and again.headers["Idempotent-Replayed"] == "true"
    assert len(tasks.sent) == 1
    other = await client.post("/api/v1/jobs", json={"video_id": str(uuid.uuid4())}, headers=headers)
    assert_problem(other, 422, "IDEMPOTENCY_KEY_REUSED")
    missing = await client.post("/api/v1/jobs", json={"video_id": str(vid)}, headers=who(org))
    assert_problem(missing, 400, "IDEMPOTENCY_KEY_REQUIRED")


async def test_one_active_job_per_video(client: httpx.AsyncClient, migrated: Db) -> None:
    org = uuid.uuid4()
    vid = await _video(migrated, org)
    job = await _create(client, org, vid)
    r = await client.post("/api/v1/jobs", json={"video_id": str(vid)}, headers=idem(org))
    assert assert_problem(r, 409, "CONFLICT")["job_id"] == job["id"]


@pytest.mark.parametrize(("status", "source", "ok"), [
    ("uploading", "upload", False), ("failed", "upload", False), ("ingesting", "youtube", True),
])  # fmt: skip
async def test_video_must_be_ready(client: httpx.AsyncClient, migrated: Db, status: str, source: str, ok: bool) -> None:
    org = uuid.uuid4()
    vid = await _video(migrated, org, status, source)
    r = await client.post("/api/v1/jobs", json={"video_id": str(vid)}, headers=idem(org))
    if ok:  # YouTube still downloading: the job waits in `ingesting`
        assert r.status_code == 202 and r.json()["status"] == "queued"
    else:
        assert assert_problem(r, 409, "CONFLICT")["reason"] == "video_not_ready"
    r = await client.post("/api/v1/jobs", json={"video_id": str(uuid.uuid4())}, headers=idem(org))
    assert_problem(r, 404, "NOT_FOUND")


async def test_role_and_config_validation(client: httpx.AsyncClient, tasks: FakeTasks, migrated: Db) -> None:
    org = uuid.uuid4()
    vid = await _video(migrated, org)
    r = await client.post("/api/v1/jobs", json={"video_id": str(vid)}, headers=idem(org, "viewer"))
    assert_problem(r, 403, "FORBIDDEN")
    r = await client.post("/api/v1/jobs", json={"video_id": str(vid), "config": {"frame_stride": 3}}, headers=idem(org))
    body = assert_problem(r, 422, "VALIDATION_FAILED")
    assert body["errors"][0]["loc"] == ["body", "config", "frame_stride"]
    r = await client.post("/api/v1/jobs", json={"video_id": str(vid), "config": {"nope": 1}}, headers=idem(org))
    assert_problem(r, 422, "VALIDATION_FAILED")
    assert tasks.sent == []


async def test_queue_down_rolls_back(client: httpx.AsyncClient, tasks: FakeTasks, migrated: Db) -> None:
    org = uuid.uuid4()
    vid = await _video(migrated, org)
    tasks.down = True
    assert_problem(await client.post("/api/v1/jobs", json={"video_id": str(vid)}, headers=idem(org)), 503, "INTERNAL")
    assert await _exec(migrated, org, lambda s: s.scalar(select(Job.id))) is None


async def test_cancel(client: httpx.AsyncClient, tasks: FakeTasks, migrated: Db) -> None:
    org = uuid.uuid4()
    vid = await _video(migrated, org)
    job = await _create(client, org, vid)
    assert_problem(await client.post(f"/api/v1/jobs/{job['id']}/cancel", headers=who(org, "viewer")), 403, "FORBIDDEN")
    r = await client.post(f"/api/v1/jobs/{job['id']}/cancel", headers=who(org))
    assert r.status_code == 200 and r.json()["status"] == "cancelled" and r.json()["finished_at"]
    assert tasks.sent[-1] == ("orchestrate.cancel_job", {"job_id": job["id"], "org_id": str(org)}, "orchestrate")
    assert_problem(await client.post(f"/api/v1/jobs/{job['id']}/cancel", headers=who(org)), 409, "CONFLICT")
    # the video is free again
    await _create(client, org, vid)


async def _fail(db: Db, org: uuid.UUID, job_id: str, retryable: bool) -> None:
    jid = uuid.UUID(job_id)

    async def fail(s: Any) -> None:
        err = {"code": "INTERNAL" if retryable else "NO_WIDE_SHOTS", "retryable": retryable, "detail": "x"}
        await s.execute(update(Job).where(Job.id == jid).values(status="failed", error=err, stage="infer"))
        s.add_all([
            JobTask(job_id=jid, org_id=org, task_key="normalize", status="done", attempts=1),
            JobTask(job_id=jid, org_id=org, task_key="infer:001", status="failed", attempts=4, error="boom",
                    external_id="celery-1"),
        ])  # fmt: skip

    await _exec(db, org, fail)


async def test_retry_resumes_failed_job(client: httpx.AsyncClient, tasks: FakeTasks, migrated: Db) -> None:
    org = uuid.uuid4()
    vid = await _video(migrated, org)
    job = await _create(client, org, vid)
    assert_problem(await client.post(f"/api/v1/jobs/{job['id']}/retry", headers=who(org)), 409, "CONFLICT")
    await _fail(migrated, org, job["id"], retryable=True)
    failed = (await client.get(f"/api/v1/jobs/{job['id']}", headers=who(org))).json()
    assert failed["error"] == {"code": "INTERNAL", "retryable": True, "detail": "x"}
    r = await client.post(f"/api/v1/jobs/{job['id']}/retry", headers=who(org))
    assert r.status_code == 202 and r.json()["status"] == "queued" and r.json()["error"] is None
    assert r.json()["stage"] is None  # not the stage that failed: nothing runs until the orchestrator picks it up
    assert tasks.sent[-1] == ("orchestrate.start_job", {"job_id": job["id"], "org_id": str(org)}, "orchestrate")
    rows = await _exec(migrated, org, lambda s: s.execute(select(JobTask.task_key, JobTask.status, JobTask.attempts,
                                                                       JobTask.external_id)))  # fmt: skip
    assert sorted(map(tuple, rows)) == [("infer:001", "queued", 0, None), ("normalize", "done", 1, None)]


async def test_retry_refuses_non_retryable(client: httpx.AsyncClient, migrated: Db) -> None:
    org = uuid.uuid4()
    vid = await _video(migrated, org)
    job = await _create(client, org, vid)
    await _fail(migrated, org, job["id"], retryable=False)
    r = await client.post(f"/api/v1/jobs/{job['id']}/retry", headers=who(org))
    assert assert_problem(r, 409, "CONFLICT")["reason"] == "NO_WIDE_SHOTS"
