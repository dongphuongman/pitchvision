"""W3 videos API: latest_job on list/get (one query), PATCH rename, DELETE (cancel job, abort upload, erase objects)."""

import uuid
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import httpx
import pytest
from pitchvision_api.db.models import Job, Video
from pitchvision_api.main import create_app
from pitchvision_api.settings import Settings
from sqlalchemy import event, select

from .conftest import S3, Db, s3_client
from .test_app import assert_problem
from .test_ingest_api import FakeTasks, _client, who
from .test_jobs_api import _create, _exec, _video, idem

MIB = 1024 * 1024


@pytest.fixture
def tasks() -> FakeTasks:
    return FakeTasks()


@pytest.fixture
async def client(migrated: Db, s3: S3, tasks: FakeTasks) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(
        env="local",
        dev_auth=True,
        database_url=migrated.app_url,
        image_tag="sha-test",
        storage_endpoint=s3.endpoint,
        storage_bucket=s3.bucket,
        storage_access_key=s3.access_key,
        storage_secret_key=s3.secret_key,
        storage_region="us-east-1",
        max_upload_bytes=200 * MIB,
    )
    app = create_app(settings)
    app.state.tasks = tasks
    async for c in _client(app):
        yield c


async def _job(db: Db, org: uuid.UUID, vid: uuid.UUID, **values: Any) -> uuid.UUID:
    async def add(s: Any) -> uuid.UUID:
        j = Job(video_id=vid, org_id=org, image_tag="sha-test", config={}, **values)
        s.add(j)
        await s.flush()
        return j.id

    jid: uuid.UUID = await _exec(db, org, add)
    return jid


class StatementLog:
    """Every SQL statement the API's engine runs (asyncpg under the sync facade)."""

    def __init__(self, app: Any) -> None:
        self.sql: list[str] = []
        event.listen(app.state.engine.sync_engine, "before_cursor_execute", self._on)

    def _on(self, conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        self.sql.append(" ".join(statement.split()))

    def touching(self, table: str) -> list[str]:
        return [s for s in self.sql if f"FROM {table}" in s or f"JOIN {table}" in s]


async def test_list_carries_latest_job_in_one_query(client: httpx.AsyncClient, migrated: Db) -> None:
    org = uuid.uuid4()
    never = await _video(migrated, org)
    analysed = await _video(migrated, org)
    running = await _video(migrated, org)
    await _job(migrated, org, analysed, status="failed", error={"code": "INTERNAL", "retryable": True})
    newest = await _job(migrated, org, analysed, status="done", progress=Decimal(100), stage="load_db",
                        coverage={"main_wide_pct": 71.5}, detail={"pitch_calibration": "degraded"})  # fmt: skip
    live = await _job(migrated, org, running, status="inferring", progress=Decimal("42.5"), stage="infer")
    await _video(migrated, uuid.uuid4())  # another org: invisible

    log = StatementLog(client._transport.app)  # type: ignore[attr-defined]
    r = await client.get("/api/v1/videos", headers=who(org, "viewer"))
    assert r.status_code == 200, r.text
    items = {v["id"]: v for v in r.json()["items"]}
    assert set(items) == {str(never), str(analysed), str(running)}
    assert items[str(never)]["latest_job"] is None
    assert items[str(analysed)]["latest_job"] == {
        "id": str(newest), "status": "done", "progress": 100.0, "stage": "load_db", "error": None,
        "coverage": {"main_wide_pct": 71.5}, "pitch_calibration": "degraded",
    }  # fmt: skip
    assert items[str(running)]["latest_job"]["id"] == str(live)
    assert items[str(running)]["latest_job"]["pitch_calibration"] is None  # until W4 calibrates the pitch
    # no N+1: the page and every latest_job come from ONE statement
    assert len(log.touching("videos")) == 1 and log.touching("jobs") == log.touching("videos")

    one = await client.get(f"/api/v1/videos/{analysed}", headers=who(org, "viewer"))
    assert one.json()["latest_job"] == items[str(analysed)]["latest_job"]


async def test_list_pages_with_latest_job(client: httpx.AsyncClient, migrated: Db) -> None:
    org = uuid.uuid4()
    vids = [await _video(migrated, org) for _ in range(3)]
    for v in vids:
        await _job(migrated, org, v, status="done", progress=Decimal(100))
    first = (await client.get("/api/v1/videos?limit=2", headers=who(org))).json()
    second = (await client.get(f"/api/v1/videos?limit=2&cursor={first['next_cursor']}", headers=who(org))).json()
    got = [v["id"] for v in first["items"] + second["items"]]
    assert sorted(got) == sorted(map(str, vids)) and second["next_cursor"] is None
    assert all(v["latest_job"]["status"] == "done" for v in first["items"] + second["items"])


async def test_rename(client: httpx.AsyncClient, migrated: Db) -> None:
    org = uuid.uuid4()
    vid = await _video(migrated, org)
    url = f"/api/v1/videos/{vid}"
    assert_problem(await client.patch(url, json={"title": "x"}, headers=who(org, "viewer")), 403, "FORBIDDEN")
    for bad in ("", "   ", "y" * 201):
        assert_problem(await client.patch(url, json={"title": bad}, headers=who(org)), 422, "VALIDATION_FAILED")
    assert_problem(await client.patch(url, json={}, headers=who(org)), 422, "VALIDATION_FAILED")
    r = await client.patch(url, json={"title": "  Cup final  "}, headers=who(org))
    assert r.status_code == 200 and r.json()["title"] == "Cup final" and "latest_job" in r.json()
    assert (await client.get(url, headers=who(org))).json()["title"] == "Cup final"
    assert (await client.patch(url, json={"title": "z" * 200}, headers=who(org))).status_code == 200
    other = await client.patch(url, json={"title": "mine"}, headers=who(uuid.uuid4()))
    assert_problem(other, 404, "NOT_FOUND")


async def test_delete_cancels_job_enqueues_erasure_and_404s(
    client: httpx.AsyncClient, tasks: FakeTasks, migrated: Db
) -> None:
    org = uuid.uuid4()
    vid = await _video(migrated, org)
    keep = await _video(migrated, org)
    job = await _create(client, org, vid)
    url = f"/api/v1/videos/{vid}"
    assert_problem(await client.delete(url, headers=who(org, "viewer")), 403, "FORBIDDEN")
    assert_problem(await client.delete(url, headers=who(uuid.uuid4())), 404, "NOT_FOUND")

    r = await client.delete(url, headers=who(org))
    assert r.status_code == 204 and r.content == b""
    # the active job went through the cancel path (orchestrator revokes + publishes `cancelled`) …
    assert (await client.get(f"/api/v1/jobs/{job['id']}", headers=who(org))).json()["status"] == "cancelled"
    assert tasks.sent[-2:] == [
        ("orchestrate.cancel_job", {"job_id": job["id"], "org_id": str(org)}, "orchestrate"),
        ("cpu.delete_video_objects", {"org_id": str(org), "video_id": str(vid)}, "cpu"),
    ]
    # … the row is soft-deleted …
    row = await _exec(migrated, org, lambda s: s.scalar(select(Video).where(Video.id == vid)))
    assert row.status == "deleted" and row.deleted_at is not None
    # … and the video is gone from every read path
    assert_problem(await client.get(url, headers=who(org)), 404, "NOT_FOUND")
    assert_problem(await client.get(f"{url}/jobs", headers=who(org)), 404, "NOT_FOUND")
    assert_problem(await client.patch(url, json={"title": "x"}, headers=who(org)), 404, "NOT_FOUND")
    assert_problem(await client.delete(url, headers=who(org)), 404, "NOT_FOUND")
    listed = (await client.get("/api/v1/videos", headers=who(org))).json()["items"]
    assert [v["id"] for v in listed] == [str(keep)]
    r = await client.post("/api/v1/jobs", json={"video_id": str(vid)}, headers=idem(org))
    assert_problem(r, 404, "NOT_FOUND")


async def test_delete_without_job_and_queue_down(client: httpx.AsyncClient, tasks: FakeTasks, migrated: Db) -> None:
    org = uuid.uuid4()
    vid = await _video(migrated, org)
    tasks.down = True  # the erasure can't be queued → 503 and nothing changes
    assert_problem(await client.delete(f"/api/v1/videos/{vid}", headers=who(org)), 503, "INTERNAL")
    assert (await client.get(f"/api/v1/videos/{vid}", headers=who(org))).status_code == 200
    tasks.down = False
    assert (await client.delete(f"/api/v1/videos/{vid}", headers=who(org))).status_code == 204
    assert [n for n, _, _ in tasks.sent] == ["cpu.delete_video_objects"]  # no job → nothing to cancel


async def test_delete_aborts_in_progress_upload(client: httpx.AsyncClient, tasks: FakeTasks, s3: S3) -> None:
    org = uuid.uuid4()
    r = await client.post("/api/v1/videos/uploads", json={"filename": "m.mp4", "size": 20 * MIB, "mime": "video/mp4"},
                          headers=who(org))  # fmt: skip
    assert r.status_code == 201, r.text
    up = r.json()
    async with httpx.AsyncClient(timeout=60) as raw:
        assert (await raw.put(up["parts"][0]["url"], content=b"\0" * (16 * MIB))).status_code == 200
    key = f"source/orgs/{org}/videos/{up['video_id']}/original.mp4"
    s3c = s3_client(s3.endpoint)
    assert s3c.list_parts(Bucket=s3.bucket, Key=key, UploadId=up["upload_id"])["Parts"]

    assert (await client.delete(f"/api/v1/videos/{up['video_id']}", headers=who(org))).status_code == 204
    with pytest.raises(Exception, match="NoSuchUpload"):
        s3c.list_parts(Bucket=s3.bucket, Key=key, UploadId=up["upload_id"])
    assert tasks.sent[-1][0] == "cpu.delete_video_objects"
