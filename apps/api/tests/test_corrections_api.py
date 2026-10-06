"""SCR-14 team confirmation / manual team samples, SCR-15 manual calibration, frame stills, media URL, pitch template
— real Postgres (RLS) + S3; the worker side is a FakeTasks queue."""

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from pitchvision_api.db.models import Correction, Job, ShotRow, Team, Video
from pitchvision_api.main import create_app
from pitchvision_api.services.corrections import general_position
from pitchvision_api.services.tasks import TASK_EXTRACT_FRAME, TASK_START_JOB
from pitchvision_api.settings import Settings
from pitchvision_schemas import Manifest, PipelineConfig, keys
from sqlalchemy import select

from .conftest import S3, Db
from .test_app import assert_problem
from .test_ingest_api import FakeTasks, who
from .test_jobs_api import _exec, idem
from .test_results_api import KEY, _publish, _put

GRID = {"tile_w": 48, "tile_h": 64, "label_w": 120, "per_row": 16, "width": 888, "height": 192,
        "rows": [{"label": "team 0", "cluster": 0, "count": 16}, {"label": "team 1", "cluster": 1, "count": 5},
                 {"label": "no team", "cluster": -1, "count": 2}]}  # fmt: skip


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


async def _analysed(db: Db, org: uuid.UUID, *, job_status: str = "done", teams: bool = True,
                    width: int | None = 1920, height: int | None = 1080) -> tuple[uuid.UUID, uuid.UUID]:
    """A ready video with one finished job, 2 teams and a main_wide shot 0–20 s + close-up 20–25 s."""
    async def add(s: Any) -> tuple[uuid.UUID, uuid.UUID]:
        v = Video(org_id=org, created_by=uuid.uuid4(), status="ready", source_type="upload", title="m", width=width,
                  height=height)  # fmt: skip
        s.add(v)
        await s.flush()
        j = Job(video_id=v.id, org_id=org, image_tag="t", config=PipelineConfig().model_dump(mode="json"),
                status=job_status, detail={"pitch_calibration": "degraded"},
                finished_at=datetime.now(UTC))  # fmt: skip
        s.add(j)
        s.add(ShotRow(video_id=v.id, org_id=org, t0_ms=0, t1_ms=20_000, type="main_wide", conf=0.9))
        s.add(ShotRow(video_id=v.id, org_id=org, t0_ms=20_000, t1_ms=25_000, type="close_up", conf=0.9))
        if teams:
            s.add(Team(video_id=v.id, org_id=org, cluster=0, name="Team A", color="#e53935"))
            s.add(Team(video_id=v.id, org_id=org, cluster=1, name="Team B", color="#1e88e5"))
        await s.flush()
        return v.id, j.id

    ids: tuple[uuid.UUID, uuid.UUID] = await _exec(db, org, add)
    return ids


async def _rows(db: Db, org: uuid.UUID, model: Any, **where: Any) -> list[Any]:
    async def q(s: Any) -> list[Any]:
        stmt = select(model).where(*(getattr(model, k) == v for k, v in where.items()))
        return list((await s.scalars(stmt)).all())

    rows: list[Any] = await _exec(db, org, q)
    return rows


# ---------------------------------------------------------------- SCR-14 confirm
async def test_teams_with_sample_crops_and_confirm(client: httpx.AsyncClient, migrated: Db, s3: S3) -> None:
    org = uuid.uuid4()
    vid, job = await _analysed(migrated, org)
    models = keys.job_results_prefix(str(org), str(vid), str(job)) + "models/"
    _put(s3, models + "team_model.json", json.dumps({"format": "pv-team-model/v1", "stats": {"grid": GRID}}).encode())
    r = await client.get(f"/api/v1/results/{vid}/teams", headers=who(org, "viewer"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["job_id"] == str(job)
    sheet = body["sample_sheet"]
    assert "team_samples.jpg" in sheet["url"] and "X-Amz-Signature" in sheet["url"] and sheet["expires_in"] == 900
    assert (sheet["width"], sheet["height"]) == (888, 192)
    a, b = body["items"]
    assert len(a["samples"]) == 8 and a["samples"][1] == {"x": 168, "y": 0, "w": 48, "h": 64}
    assert len(b["samples"]) == 5 and b["samples"][0] == {"x": 120, "y": 64, "w": 48, "h": 64}

    # viewer can't write; validation is problem+json
    patch = {"teams": [{"cluster": 0, "name": "France", "color": "#0055A4"}], "confirmed": True}
    assert_problem(await client.patch(f"/api/v1/results/{vid}/teams", json=patch, headers=who(org, "viewer")), 403,
                   "FORBIDDEN")
    for bad in ({"teams": [{"cluster": 0, "name": "x" * 41, "color": "#000000"}]},
                {"teams": [{"cluster": 0, "name": "   ", "color": "#000000"}]},
                {"teams": [{"cluster": 0, "name": "A", "color": "red"}]},
                {"teams": [{"cluster": 2, "name": "A", "color": "#000000"}]},
                {"teams": [{"cluster": 0, "name": "A", "color": "#000000"}, {"cluster": 0, "name": "B",
                                                                            "color": "#111111"}]}):  # fmt: skip
        assert_problem(await client.patch(f"/api/v1/results/{vid}/teams", json=bad, headers=who(org)), 422,
                       "VALIDATION_FAILED")

    r = await client.patch(f"/api/v1/results/{vid}/teams", json=patch, headers=who(org))
    assert r.status_code == 200, r.text
    assert [(t["cluster"], t["name"], t["color"], t["confirmed"]) for t in r.json()["items"]] == [
        (0, "France", "#0055a4", True), (1, "Team B", "#1e88e5", True)]
    assert r.json()["items"][0]["samples"]  # same shape as GET

    # swap: names + colours change clusters; the manifest (tiles carry clusters) follows without reprocessing
    _publish(s3, org, vid, job)
    r = await client.patch(f"/api/v1/results/{vid}/teams", json={"swap": True}, headers=who(org))
    assert [(t["cluster"], t["name"]) for t in r.json()["items"]] == [(0, "Team B"), (1, "France")]
    m = Manifest.model_validate((await client.get(f"/api/v1/results/{vid}/manifest", headers=who(org))).json())
    assert [(t.cluster, t.name, t.color, t.confirmed) for t in m.teams] == [(0, "Team B", "#1e88e5", True),
                                                                            (1, "France", "#0055a4", True)]
    assert m.revision == 0 and "/r0/" in m.tile_url_template
    video = (await _rows(migrated, org, Video, id=vid))[0]
    assert video.revision == 0  # display-only: no new tiles
    corr = await _rows(migrated, org, Correction, video_id=vid)
    assert sorted(c.type for c in corr) == ["teams", "teams"] and all(c.revision == 0 for c in corr)


async def test_teams_patch_rls_and_no_teams(client: httpx.AsyncClient, migrated: Db) -> None:
    org = uuid.uuid4()
    vid, _ = await _analysed(migrated, org)
    body = {"teams": [{"cluster": 0, "name": "X", "color": "#000000"}]}
    assert_problem(await client.patch(f"/api/v1/results/{vid}/teams", json=body, headers=who(uuid.uuid4())), 404,
                   "NOT_FOUND")
    vid2, _ = await _analysed(migrated, org, teams=False)
    p = assert_problem(await client.patch(f"/api/v1/results/{vid2}/teams", json=body, headers=who(org)), 409,
                       "CONFLICT")
    assert p["reason"] == "no_teams"
    r = await client.get(f"/api/v1/results/{vid2}/teams", headers=who(org))  # no model object: no sheet
    assert r.status_code == 200 and r.json()["items"] == [] and r.json()["sample_sheet"] is None


# ---------------------------------------------------------------- SCR-14 manual
SEEDS = {"0": [{"t_ms": 1000, "box": [10, 20, 40, 90]}, {"t_ms": 1040, "box": [100, 20, 130, 90]}],
         "1": [{"t_ms": 2000, "box": [200, 20, 230, 90]}, {"t_ms": 2000, "box": [300, 20, 330, 90]}]}


async def test_manual_team_samples_start_a_seeded_job(client: httpx.AsyncClient, migrated: Db,
                                                      tasks: FakeTasks) -> None:  # fmt: skip
    org = uuid.uuid4()
    vid, parent = await _analysed(migrated, org, job_status="failed")
    url = f"/api/v1/results/{vid}/teams/manual"
    assert_problem(await client.post(url, json={"samples": SEEDS}, headers=who(org)), 400, "IDEMPOTENCY_KEY_REQUIRED")
    assert_problem(await client.post(url, json={"samples": SEEDS}, headers=idem(org, "viewer")), 403, "FORBIDDEN")
    one = {"0": SEEDS["0"][:1], "1": SEEDS["1"]}
    assert_problem(await client.post(url, json={"samples": one}, headers=idem(org)), 422, "VALIDATION_FAILED")
    bad_box = {"0": [{"t_ms": 0, "box": [50, 50, 40, 90]}] * 2, "1": SEEDS["1"]}
    assert_problem(await client.post(url, json={"samples": bad_box}, headers=idem(org)), 422, "VALIDATION_FAILED")

    h = idem(org)
    r = await client.post(url, json={"samples": SEEDS}, headers=h)
    assert r.status_code == 202, r.text
    out = r.json()
    assert out["kind"] == "team_rerun" and out["parent_job_id"] == str(parent) and out["status"] == "queued"
    assert tasks.sent[-1][0] == TASK_START_JOB and tasks.sent[-1][1]["job_id"] == out["id"]
    replay = await client.post(url, json={"samples": SEEDS}, headers=h)
    assert replay.status_code == 202 and replay.json()["id"] == out["id"]
    job = (await _rows(migrated, org, Job, id=uuid.UUID(out["id"])))[0]
    assert job.detail["team_seeds"] == SEEDS and job.config == PipelineConfig().model_dump(mode="json")
    corr = (await _rows(migrated, org, Correction, video_id=vid, type="team_samples"))[0]
    assert corr.payload["job_id"] == out["id"] and corr.payload["samples"] == SEEDS
    # one active job per video
    p = assert_problem(await client.post(url, json={"samples": SEEDS}, headers=idem(org)), 409, "CONFLICT")
    assert p["job_id"] == out["id"]


async def test_team_candidates(client: httpx.AsyncClient, migrated: Db, s3: S3) -> None:
    org = uuid.uuid4()
    vid, job = await _analysed(migrated, org, job_status="failed")
    assert_problem(await client.get(f"/api/v1/results/{vid}/teams/candidates", headers=who(org)), 404, "NOT_FOUND")

    async def calibrate_failed(s: Any) -> None:
        from pitchvision_api.db.models import JobTask

        s.add(JobTask(job_id=job, org_id=org, task_key="calibrate", status="failed"))

    await _exec(migrated, org, calibrate_failed)
    cand = {"fps": 25, "frames": [{"frame_idx": 25, "t_ms": 1000, "boxes": [[1, 2, 30, 80], [50, 2, 80, 80]]}]}
    _put(s3, keys.job_results_prefix(str(org), str(vid), str(job)) + "models/diagnostics/team_candidates.json",
         json.dumps(cand).encode())
    r = await client.get(f"/api/v1/results/{vid}/teams/candidates", headers=who(org, "viewer"))
    assert r.status_code == 200, r.text
    assert r.json() == {"job_id": str(job), "fps": 25.0, "frames": cand["frames"]}


# ---------------------------------------------------------------- SCR-15
def _pts(frame_ms: int = 5000, idx: tuple[int, ...] = (0, 24, 29, 5)) -> list[dict[str, Any]]:
    img = {0: (100, 100), 24: (1100, 100), 29: (1200, 650), 5: (50, 650), 13: (600, 100), 1: (90, 200)}
    return [{"template_index": i, "x": img[i][0], "y": img[i][1], "frame_ms": frame_ms} for i in idx]


def test_general_position() -> None:
    assert general_position([(0, 0), (10, 0), (10, 10), (0, 10)])
    assert not general_position([(0, 0), (5, 0), (10, 0), (0, 10)])          # 3 on a line
    assert general_position([(0, 0), (5, 0), (10, 0), (0, 10), (10, 10)])   # …but another 4 work
    assert not general_position([(0, 0), (1, 1), (2, 2), (3, 3.001)])


async def test_calibration_validation(client: httpx.AsyncClient, migrated: Db) -> None:
    org = uuid.uuid4()
    vid, _ = await _analysed(migrated, org)
    url = f"/api/v1/results/{vid}/calibration"

    async def post(points: list[dict[str, Any]], role: str = "editor") -> httpx.Response:
        return await client.post(url, json={"points": points, "scope": "shot"}, headers=idem(org, role))

    assert_problem(await post(_pts(), "viewer"), 403, "FORBIDDEN")
    assert_problem(await post(_pts()[:3]), 422, "VALIDATION_FAILED")
    assert_problem(await post([{**p, "template_index": 32} for p in _pts()]), 422, "VALIDATION_FAILED")
    cases = {
        "frame_ms": [*_pts()[:3], *_pts(6000)[3:]],
        "duplicate_landmark": [*_pts(), {**_pts()[0], "x": 5}],
        "collinear": _pts(idx=(0, 13, 24, 1)),  # 0, 13, 24 on the far touchline …
        "not_analysed_shot": _pts(21_000),        # inside the close-up
    }
    cases["collinear"][3] = {**cases["collinear"][3], "x": 1500, "y": 100}  # … and all 4 on one image line
    for reason, points in cases.items():
        p = assert_problem(await post(points), 422, "VALIDATION_FAILED")
        assert p["reason"] == reason, (reason, p)
    other = assert_problem(await client.post(url, json={"points": _pts()}, headers=idem(uuid.uuid4())), 404,
                           "NOT_FOUND")  # RLS
    assert other["code"] == "NOT_FOUND"
    nothing = await _analysed(migrated, org, job_status="failed")
    p = assert_problem(await client.post(f"/api/v1/results/{nothing[0]}/calibration", json={"points": _pts()},
                                         headers=idem(org)), 409, "CONFLICT")  # fmt: skip
    assert p["reason"] == "no_analysis"


async def test_calibration_starts_recalibrate_job(client: httpx.AsyncClient, migrated: Db, tasks: FakeTasks) -> None:
    org = uuid.uuid4()
    vid, parent = await _analysed(migrated, org)
    url = f"/api/v1/results/{vid}/calibration"
    st = await client.get(url, headers=who(org, "viewer"))
    assert st.json() == {"pitch_calibration": "degraded", "job_id": str(parent), "active_job": None,
                         "last_manual": None}  # fmt: skip
    assert_problem(await client.post(url, json={"points": _pts()}, headers=who(org)), 400, "IDEMPOTENCY_KEY_REQUIRED")
    r = await client.post(url, json={"points": _pts(), "scope": "video"}, headers=idem(org))
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["revision"] == 1 and body["job"]["kind"] == "recalibrate"
    assert body["job"]["parent_job_id"] == str(parent)
    assert tasks.sent[-1] == (TASK_START_JOB, {"job_id": body["job"]["id"], "org_id": str(org)}, "orchestrate")
    job = (await _rows(migrated, org, Job, id=uuid.UUID(body["job"]["id"])))[0]
    assert job.detail["calibration"] == {"points": _pts(), "frame_ms": 5000, "scope": "video"}
    assert job.detail["correction_id"] == body["correction_id"]
    assert (await _rows(migrated, org, Video, id=vid))[0].revision == 1
    corr = (await _rows(migrated, org, Correction, video_id=vid, type="calibration"))[0]
    assert corr.revision == 1 and corr.payload["shot"] == [0, 20_000] and corr.payload["job_id"] == body["job"]["id"]

    st = (await client.get(url, headers=who(org, "viewer"))).json()
    assert st["active_job"]["id"] == body["job"]["id"] and st["last_manual"]["scope"] == "video"
    assert st["last_manual"]["points"] == _pts() and st["last_manual"]["revision"] == 1
    p = assert_problem(await client.post(url, json={"points": _pts()}, headers=idem(org)), 409, "CONFLICT")
    assert p["job_id"] == body["job"]["id"]
    assert (await _rows(migrated, org, Video, id=vid))[0].revision == 1  # the refused request rolled back


async def test_calibration_after_failed_recalibration(client: httpx.AsyncClient, migrated: Db, tasks: FakeTasks
                                                      ) -> None:  # fmt: skip
    """SCR-13 E11: a recalibrate job that failed HOMOGRAPHY_UNAVAILABLE leaves the analysis live, so the user can
    calibrate again; the new job's parent is the last DONE job, never the failed one."""
    org = uuid.uuid4()
    vid, parent = await _analysed(migrated, org)

    async def failed(s: Any) -> uuid.UUID:
        v = await s.get(Video, vid)
        v.revision = 1  # the failed attempt had bumped it
        j = Job(video_id=vid, org_id=org, image_tag="t", config=PipelineConfig().model_dump(mode="json"),
                status="failed", detail={"kind": "recalibrate", "parent_job_id": str(parent)},
                error={"code": "HOMOGRAPHY_UNAVAILABLE", "retryable": False, "detail": None},
                finished_at=datetime.now(UTC))  # fmt: skip
        s.add(j)
        await s.flush()
        jid: uuid.UUID = j.id
        return jid

    await _exec(migrated, org, failed)
    url = f"/api/v1/results/{vid}/calibration"
    st = (await client.get(url, headers=who(org, "viewer"))).json()
    assert st["job_id"] == str(parent) and st["pitch_calibration"] == "degraded" and st["active_job"] is None
    r = await client.post(url, json={"points": _pts(), "scope": "shot"}, headers=idem(org))
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["revision"] == 2 and body["job"]["parent_job_id"] == str(parent)
    assert tasks.sent[-1][0] == TASK_START_JOB


async def test_calibration_frame_url(client: httpx.AsyncClient, migrated: Db, s3: S3, tasks: FakeTasks) -> None:
    org = uuid.uuid4()
    vid, _ = await _analysed(migrated, org)
    url = f"/api/v1/results/{vid}/calibration/frame"
    key = keys.frame_key(str(org), str(vid), 1000)
    # not cached → the worker extracts it (FakeTasks answers like cpu.extract_frame and we store the JPEG)
    _put(s3, key, b"\xff\xd8jpeg")
    tasks.probe = {"ok": True, "key": key, "t_ms": 1000}
    r = await client.get(url, params={"t_ms": 1013}, headers=who(org, "viewer"))
    assert r.status_code == 200, r.text
    assert r.json()["t_ms"] == 1000 and "frames/000001000.jpg" in r.json()["url"] and r.json()["expires_in"] == 900
    assert "X-Amz-Signature" in r.json()["url"] and not tasks.calls  # cached object: no worker call
    r = await client.get(url, params={"t_ms": 2000}, headers=who(org))
    assert r.status_code == 200 and tasks.calls[-1][:3] == (TASK_EXTRACT_FRAME, {"org_id": str(org), "video_id":
                                                                                  str(vid), "t_ms": 2000}, "cpu")
    tasks.probe = {"ok": False, "reason": "no_video"}
    assert_problem(await client.get(url, params={"t_ms": 3000}, headers=who(org)), 404, "NOT_FOUND")
    tasks.probe = {"ok": False, "reason": "out_of_range"}
    assert_problem(await client.get(url, params={"t_ms": 3000}, headers=who(org)), 422, "VALIDATION_FAILED")
    from pitchvision_api.services.tasks import TaskTimeout

    tasks.probe = TaskTimeout("busy")
    assert_problem(await client.get(url, params={"t_ms": 3000}, headers=who(org)), 503, "INTERNAL")
    assert_problem(await client.get(url, params={"t_ms": 3000}, headers=who(uuid.uuid4())), 404, "NOT_FOUND")


async def test_media_url(client: httpx.AsyncClient, migrated: Db, s3: S3) -> None:
    org = uuid.uuid4()
    vid, job = await _analysed(migrated, org)
    url = f"/api/v1/results/{vid}/media"
    p = assert_problem(await client.get(url, headers=who(org, "viewer")), 404, "NOT_FOUND")
    assert p["reason"] == "not_normalized"
    _put(s3, keys.normalized_key(str(org), str(vid)), b"mp4")
    r = await client.get(url, headers=who(org, "viewer"))
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "private, no-store"
    m = r.json()
    assert "normalized.mp4" in m["video_url"] and "X-Amz-Expires=21600" in m["video_url"]
    assert (m["expires_in"], m["fps"], m["width"], m["height"]) == (21600, 25, 1280, 720)  # from the source size

    async def normalized(s: Any) -> None:
        from pitchvision_api.db.models import JobTask

        s.add(JobTask(job_id=job, org_id=org, task_key="normalize", status="done", finished_at=datetime.now(UTC),
                      payload={"manifest": {"fps": 25, "width": 1280, "height": 544}}))  # fmt: skip

    await _exec(migrated, org, normalized)
    assert (await client.get(url, headers=who(org))).json()["height"] == 544
    assert_problem(await client.get(url, headers=who(uuid.uuid4())), 404, "NOT_FOUND")


async def test_pitch_template(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/results/pitch-template", headers=who(uuid.uuid4(), "viewer"))
    assert r.status_code == 200, r.text
    t = r.json()
    assert (t["length_m"], t["width_m"], len(t["keypoints"])) == (105.0, 68.0, 32)
    assert t["keypoints"][9] == {"index": 9, "name": "l_box_front_far", "kind": "intersection", "x": 16.5,
                                 "y": 13.84}  # fmt: skip
    assert any(ln["name"] == "centre_circle" and ln["closed"] for ln in t["lines"])
    assert (await client.get("/api/v1/results/pitch-template")).status_code == 401
