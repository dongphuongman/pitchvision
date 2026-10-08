"""M2-BE-01 track corrections (SCR-22): record, validate, schedule a debounced edit job, undo — real Postgres."""

import uuid
from typing import Any

import httpx
from pitchvision_api.db.models import Correction, Job, Player
from sqlalchemy import select

from .conftest import Db
from .test_app import assert_problem
from .test_corrections_api import SEEDS, _analysed, _pts, _rows, client, tasks  # noqa: F401 — pytest fixtures
from .test_ingest_api import FakeTasks, who
from .test_jobs_api import _exec, idem


async def _players(db: Db, org: uuid.UUID, vid: uuid.UUID, *tracks: int) -> None:
    async def add(s: Any) -> None:
        for t in tracks:
            s.add(Player(video_id=vid, org_id=org, track_id=t, role="player"))
        await s.flush()

    await _exec(db, org, add)


async def test_corrections_flow(client: httpx.AsyncClient, migrated: Db, tasks: FakeTasks) -> None:  # noqa: F811
    org = uuid.uuid4()
    vid, parent = await _analysed(migrated, org)
    await _players(migrated, org, vid, 1, 2, 7)
    url = f"/api/v1/results/{vid}/corrections"
    state = (await client.get(url, headers=who(org, "viewer"))).json()
    assert state == {"items": [], "pending": False, "job": None, "apply_after": None}

    r = await client.post(url, json={"type": "merge", "from": 2, "into": 1}, headers=idem(org))
    assert r.status_code == 202, r.text
    s1 = r.json()
    assert [(c["type"], c["payload"]) for c in s1["items"]] == [("merge", {"from": 2, "into": 1})]
    assert s1["pending"] is True and s1["job"]["status"] == "queued" and s1["apply_after"]
    jobs = await _rows(migrated, org, Job, video_id=vid)
    edit = next(j for j in jobs if j.id != parent)
    assert edit.detail["kind"] == "edit" and edit.detail["parent_job_id"] == str(parent)

    # a second correction in the burst reuses the queued job (pushes the debounce back), the split gets a fresh id
    s2 = (await client.post(url, json={"type": "split", "track": 7, "at_ms": 5000}, headers=idem(org))).json()
    assert s2["job"]["id"] == s1["job"]["id"] and s2["apply_after"] >= s1["apply_after"]
    assert s2["items"][1]["payload"] == {"track": 7, "at_ms": 5000, "new_track": 8}
    assert len(await _rows(migrated, org, Job, video_id=vid)) == 2
    # the split's new track is a valid target right away
    s3 = (await client.post(url, json={"type": "remove", "track": 8}, headers=idem(org))).json()
    assert s3["items"][2]["payload"] == {"track": 8, "from_ms": None, "to_ms": None}

    for body, reason in (({"type": "merge", "from": 1, "into": 1}, "same_track"),
                         ({"type": "remove", "track": 99}, "unknown_track"),
                         ({"type": "remove", "track": 1, "from_ms": 500, "to_ms": 400}, "range")):  # fmt: skip
        body_ = assert_problem(await client.post(url, json=body, headers=idem(org)), 422, "VALIDATION_FAILED")
        assert body_["reason"] == reason
    assert (await client.post(url, json={"type": "remove", "track": 1}, headers=idem(org, "viewer"))).status_code == 403

    undo = await client.delete(f"{url}/{s1['items'][0]['id']}", headers=who(org))
    assert undo.status_code == 202 and [c["type"] for c in undo.json()["items"]] == ["split", "remove"]
    assert (await client.delete(f"{url}/{s1['items'][0]['id']}", headers=who(org))).status_code == 404
    assert (await client.get(url, headers=who(uuid.uuid4()))).status_code == 404


async def test_pending_while_another_job_runs(client: httpx.AsyncClient, migrated: Db, tasks: FakeTasks) -> None:  # noqa: F811
    org = uuid.uuid4()
    vid, parent = await _analysed(migrated, org)
    await _players(migrated, org, vid, 1, 2)

    async def running(s: Any) -> None:  # e.g. a recalibration in progress
        s.add(Job(video_id=vid, org_id=org, image_tag="t", config={}, status="postprocessing",
                  detail={"kind": "recalibrate", "parent_job_id": str(parent)}))  # fmt: skip
        await s.flush()

    await _exec(migrated, org, running)
    url = f"/api/v1/results/{vid}/corrections"
    s = (await client.post(url, json={"type": "merge", "from": 2, "into": 1}, headers=idem(org))).json()
    assert s["pending"] is True and s["job"] is None  # recorded; it waits for the running job
    assert (await client.post(f"{url}/apply", headers=who(org))).json()["job"] is None  # still blocked


async def _finish_queued(db: Db, org: uuid.UUID, vid: uuid.UUID) -> None:
    """Mark the video's queued edit job done, so a new analysis / calibration can start."""

    async def go(s: Any) -> None:
        job = (await s.scalars(select(Job).where(Job.video_id == vid, Job.status == "queued"))).one()
        job.status = "done"
        await s.flush()

    await _exec(db, org, go)


async def test_reanalyze_clears_track_edits(client: httpx.AsyncClient, migrated: Db, tasks: FakeTasks) -> None:  # noqa: F811
    """A new `analyze` re-tracks: the old merge/split/remove name dead ids, so they are marked undone (kept)."""
    org = uuid.uuid4()
    vid, _parent = await _analysed(migrated, org)
    await _players(migrated, org, vid, 1, 2)
    url = f"/api/v1/results/{vid}/corrections"
    r = await client.post(url, json={"type": "merge", "from": 2, "into": 1}, headers=idem(org))
    assert r.status_code == 202 and len(r.json()["items"]) == 1
    await _finish_queued(migrated, org, vid)
    r = await client.post("/api/v1/jobs", json={"video_id": str(vid)}, headers=idem(org))
    assert r.status_code == 202, r.text
    assert (await client.get(url, headers=who(org, "viewer"))).json() == {
        "items": [],
        "pending": False,
        "job": None,
        "apply_after": None,
    }
    rows = await _rows(migrated, org, Correction, video_id=vid, type="merge")
    assert len(rows) == 1 and rows[0].payload.get("undone") is True


async def test_team_rerun_clears_track_edits(client: httpx.AsyncClient, migrated: Db, tasks: FakeTasks) -> None:  # noqa: F811
    """`team_rerun` re-tracks with new seeds: stale track edits die, its own team_samples record stays live."""
    org = uuid.uuid4()
    vid, _parent = await _analysed(migrated, org)
    await _players(migrated, org, vid, 1, 2)
    url = f"/api/v1/results/{vid}/corrections"
    r = await client.post(url, json={"type": "split", "track": 1, "at_ms": 5000}, headers=idem(org))
    assert r.status_code == 202
    await _finish_queued(migrated, org, vid)
    r = await client.post(f"/api/v1/results/{vid}/teams/manual", json={"samples": SEEDS}, headers=idem(org))
    assert r.status_code == 202, r.text
    state = (await client.get(url, headers=who(org, "viewer"))).json()
    assert state["items"] == [] and state["pending"] is False
    by_type = {c.type: c for c in await _rows(migrated, org, Correction, video_id=vid)}
    assert by_type["split"].payload.get("undone") is True
    assert "undone" not in by_type["team_samples"].payload


async def test_retry_analyze_clears_track_edits(client: httpx.AsyncClient, migrated: Db, tasks: FakeTasks) -> None:  # noqa: F811
    """Corrections made against the finished analysis while its re-analysis failed die on retry."""
    org = uuid.uuid4()
    vid, _parent = await _analysed(migrated, org)
    await _players(migrated, org, vid, 1, 2)

    async def failed(s: Any) -> uuid.UUID:
        j = Job(video_id=vid, org_id=org, image_tag="t", config={},
                status="failed", error={"code": "INTERNAL", "retryable": True, "detail": "x"})  # fmt: skip
        s.add(j)
        await s.flush()
        jid: uuid.UUID = j.id
        return jid

    jid = await _exec(migrated, org, failed)
    url = f"/api/v1/results/{vid}/corrections"
    r = await client.post(url, json={"type": "merge", "from": 2, "into": 1}, headers=idem(org))
    assert r.status_code == 202
    await _finish_queued(migrated, org, vid)
    r = await client.post(f"/api/v1/jobs/{jid}/retry", headers=who(org))
    assert r.status_code == 202, r.text
    assert (await client.get(url, headers=who(org, "viewer"))).json()["items"] == []


async def test_recalibrate_keeps_track_edits(client: httpx.AsyncClient, migrated: Db, tasks: FakeTasks) -> None:  # noqa: F811
    """Recalibration reuses tracks (recompute reads the unedited ancestor): edits stay live and pending."""
    org = uuid.uuid4()
    vid, _parent = await _analysed(migrated, org)
    await _players(migrated, org, vid, 1, 2)
    url = f"/api/v1/results/{vid}/corrections"
    assert (await client.post(url, json={"type": "merge", "from": 2, "into": 1}, headers=idem(org))).status_code == 202
    await _finish_queued(migrated, org, vid)
    r = await client.post(f"/api/v1/results/{vid}/calibration", json={"points": _pts(), "scope": "shot"},
                          headers=idem(org))  # fmt: skip
    assert r.status_code == 202, r.text
    state = (await client.get(url, headers=who(org, "viewer"))).json()
    assert [c["type"] for c in state["items"]] == ["merge"] and state["pending"] is True
    assert state["job"] is None  # the running job is the recalibration, not an edit
