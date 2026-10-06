"""SCR-25 / SCR-26 exports (M1-BE-17): request → frozen spec + cpu.render_export, plan rules, list / cancel / retry /
delete / download — real Postgres (RLS) + S3; the worker side is a FakeTasks queue."""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from pitchvision_api.db.models import Export, Team
from pitchvision_api.main import create_app
from pitchvision_api.services.exports import export_filename
from pitchvision_api.services.tasks import TASK_RENDER_EXPORT
from pitchvision_api.settings import Settings
from pitchvision_schemas import ExportSpec, keys

from .conftest import S3, Db
from .test_app import assert_problem
from .test_ingest_api import FakeTasks, who
from .test_jobs_api import _exec, idem
from .test_results_api import KEY, _publish, _put, _video_job

URL = "/api/v1/exports"


@pytest.fixture
def tasks() -> FakeTasks:
    return FakeTasks()


@pytest.fixture
async def client(migrated: Db, s3: S3, tasks: FakeTasks) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(env="local", dev_auth=True, database_url=migrated.app_url, storage_endpoint=s3.endpoint,
                        storage_bucket=s3.bucket, storage_access_key=s3.access_key, storage_secret_key=s3.secret_key,
                        storage_region="us-east-1", tile_signing_key=KEY, image_tag="sha-test")  # fmt: skip
    app = create_app(settings)
    app.state.tasks = tasks
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await app.state.engine.dispose()


def h(org: uuid.UUID, role: str = "viewer", plan: str = "free", user: uuid.UUID | None = None) -> dict[str, str]:
    out = idem(org, role) | {"X-Dev-Plan": plan}
    return out | ({"X-Dev-User": str(user)} if user else {})


async def _analysed(db: Db, s3: S3, org: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    vid, job = await _video_job(db, org)
    _publish(s3, org, vid, job, tiles=0)

    async def rename(s: Any) -> None:  # the teams table wins over the manifest (renamed after the analysis)
        s.add(Team(video_id=vid, org_id=org, cluster=0, name="Riverside", color="#112233"))
        s.add(Team(video_id=vid, org_id=org, cluster=1, name="Visitors", color="#445566"))

    await _exec(db, org, rename)
    return vid, job


async def _set(db: Db, org: uuid.UUID, export_id: str, **values: Any) -> None:
    async def upd(s: Any) -> None:
        e = await s.get(Export, uuid.UUID(export_id))
        for k, v in values.items():
            setattr(e, k, v)

    await _exec(db, org, upd)


def test_export_filename() -> None:
    assert export_filename("France vs Switzerland!", 0, 25_000, 25_000) == "france_vs_switzerland_full.mp4"
    assert export_filename(None, 2_040_000, 2_092_000, 5_400_000) == "video_34m00-34m52.mp4"


async def test_create_freezes_spec_and_enqueues(client: httpx.AsyncClient, migrated: Db, s3: S3,
                                                tasks: FakeTasks) -> None:  # fmt: skip
    org = uuid.uuid4()
    vid, job = await _analysed(migrated, s3, org)
    body = {"video_id": str(vid), "from_ms": 5000, "to_ms": 12_000, "layers": {"trails": True, "conf": True}}
    assert_problem(await client.post(URL, json=body, headers=who(org, "viewer")), 400, "IDEMPOTENCY_KEY_REQUIRED")
    headers = h(org)
    r = await client.post(URL, json=body, headers=headers)
    assert r.status_code == 202, r.text
    e = r.json()
    assert (e["status"], e["progress"], e["filename"], e["watermark"], e["quality"]) == (
        "queued", 0, "match_00m05-00m12.mp4", True, 720)
    assert e["video_title"] == "match" and e["layers"]["trails"] is True and e["layers"]["boxes"] is True
    assert tasks.sent[-1] == (TASK_RENDER_EXPORT, {"export_id": e["id"], "org_id": str(org)}, "cpu")
    replay = await client.post(URL, json=body, headers=headers)
    assert replay.json()["id"] == e["id"] and len(tasks.sent) == 1

    async def spec(s: Any) -> ExportSpec:
        return ExportSpec.model_validate((await s.get(Export, uuid.UUID(e["id"]))).spec)

    sp = await _exec(migrated, org, spec)
    assert (sp.job_id, sp.from_ms, sp.to_ms, sp.width, sp.height, sp.pitch_status) == (
        str(job), 5000, 12_000, 1280, 720, "unavailable")
    assert [(t.name, t.color) for t in sp.teams] == [("Riverside", "#112233"), ("Visitors", "#445566")]

    pro = await client.post(URL, json={"video_id": str(vid), "quality": 1080}, headers=h(org, plan="pro"))
    assert pro.status_code == 202 and pro.json()["watermark"] is False and pro.json()["quality"] == 1080
    assert pro.json()["filename"] == "match_full.mp4" and pro.json()["to_ms"] == 25_000


async def test_create_refusals(client: httpx.AsyncClient, migrated: Db, s3: S3) -> None:
    org = uuid.uuid4()
    vid, _ = await _analysed(migrated, s3, org)
    p = assert_problem(await client.post(URL, json={"video_id": str(vid), "quality": 1080}, headers=h(org)), 402,
                       "PLAN_REQUIRED")  # fmt: skip
    assert p["reason"] == "export_quality" and p["plan"] == "free"
    p = assert_problem(await client.post(URL, json={"video_id": str(vid), "layout": "video_map"}, headers=h(org)),
                       422, "VALIDATION_FAILED")  # fmt: skip
    assert p["reason"] == "no_pitch_map"
    p = assert_problem(await client.post(URL, json={"video_id": str(vid), "from_ms": 30_000}, headers=h(org)), 422,
                       "VALIDATION_FAILED")  # fmt: skip
    assert p["reason"] == "range"
    assert_problem(await client.post(URL, json={"video_id": str(vid), "from_ms": 9, "to_ms": 5}, headers=h(org)),
                   422, "VALIDATION_FAILED")  # fmt: skip
    assert_problem(await client.post(URL, json={"video_id": str(vid)}, headers=h(uuid.uuid4())), 404, "NOT_FOUND")
    failed, _ = await _video_job(migrated, org, status="failed")
    p = assert_problem(await client.post(URL, json={"video_id": str(failed)}, headers=h(org)), 409, "CONFLICT")
    assert p["reason"] == "no_analysis"


async def test_list_cancel_retry_delete(client: httpx.AsyncClient, migrated: Db, s3: S3, tasks: FakeTasks) -> None:
    org, me, other = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    vid, _ = await _analysed(migrated, s3, org)
    ids = []
    for _ in range(3):
        r = await client.post(URL, json={"video_id": str(vid)}, headers=h(org, user=me))
        ids.append(r.json()["id"])
    page = (await client.get(URL, params={"limit": 2}, headers=who(org, "viewer"))).json()
    assert [e["id"] for e in page["items"]] == ids[::-1][:2] and page["next_cursor"]
    rest = (await client.get(URL, params={"limit": 2, "cursor": page["next_cursor"]}, headers=who(org))).json()
    assert [e["id"] for e in rest["items"]] == [ids[0]] and rest["next_cursor"] is None
    assert (await client.get(URL, headers=who(uuid.uuid4()))).json()["items"] == []  # RLS

    # someone else's export: a viewer may not touch it, an editor may
    assert_problem(await client.post(f"{URL}/{ids[0]}/cancel", headers=h(org, user=other)), 403, "FORBIDDEN")
    r = await client.post(f"{URL}/{ids[0]}/cancel", headers=h(org, user=me))
    assert r.json()["status"] == "cancelled"
    assert_problem(await client.post(f"{URL}/{ids[0]}/cancel", headers=h(org, user=me)), 409, "CONFLICT")
    assert_problem(await client.post(f"{URL}/{ids[1]}/retry", headers=h(org, user=me)), 409, "CONFLICT")
    sent = len(tasks.sent)
    r = await client.post(f"{URL}/{ids[0]}/retry", headers=h(org, "editor", user=other))
    assert r.status_code == 202 and r.json()["status"] == "queued" and len(tasks.sent) == sent + 1
    active = (await client.get(URL, params={"status": "active"}, headers=who(org))).json()["items"]
    assert len(active) == 3
    assert (await client.get(URL, params={"status": "done"}, headers=who(org))).json()["items"] == []

    assert (await client.delete(f"{URL}/{ids[2]}", headers=h(org, user=me))).status_code == 204
    assert_problem(await client.get(f"{URL}/{ids[2]}", headers=who(org)), 404, "NOT_FOUND")

    async def row(s: Any) -> Export:
        return await s.get(Export, uuid.UUID(ids[2]))

    gone = await _exec(migrated, org, row)
    assert gone.status == "cancelled" and gone.deleted_at is not None


async def test_download_and_expiry(client: httpx.AsyncClient, migrated: Db, s3: S3) -> None:
    org = uuid.uuid4()
    vid, _ = await _analysed(migrated, s3, org)
    e = (await client.post(URL, json={"video_id": str(vid)}, headers=h(org))).json()
    p = assert_problem(await client.get(f"{URL}/{e['id']}/download", headers=who(org)), 409, "CONFLICT")
    assert p["reason"] == "not_ready"
    key = keys.export_key(str(org), str(vid), e["id"], "mp4")
    _put(s3, key, b"mp4-bytes")
    now = datetime.now(UTC)
    await _set(migrated, org, e["id"], status="done", progress=100, object_key=key, size_bytes=9, finished_at=now,
               expires_at=now + timedelta(days=7))  # fmt: skip
    r = await client.get(f"{URL}/{e['id']}/download", headers=who(org, "viewer"))
    assert r.status_code == 200 and r.json()["filename"] == "match_full.mp4" and r.json()["expires_in"] == 900
    q = parse_qs(urlparse(r.json()["url"]).query)
    assert q["response-content-disposition"] == ['attachment; filename="match_full.mp4"']
    async with httpx.AsyncClient() as raw:
        got = await raw.get(r.json()["url"])
    assert got.content == b"mp4-bytes"
    assert (await client.get(f"{URL}/{e['id']}", headers=who(org))).json()["status"] == "done"

    await _set(migrated, org, e["id"], expires_at=now - timedelta(seconds=1))
    assert (await client.get(f"{URL}/{e['id']}", headers=who(org))).json()["status"] == "expired"
    p = assert_problem(await client.get(f"{URL}/{e['id']}/download", headers=who(org)), 410, "NOT_FOUND")
    assert p["reason"] == "expired"
