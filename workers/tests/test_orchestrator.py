"""M1-BE-10 orchestrator against real Postgres (RLS, pv_worker) with DEV FAKE GPU stages and an in-memory object store.

Stage tasks are executed by the real executor (download → stage → upload → _SUCCESS); results are fed back through
`engine.advance(report=…)` exactly like `orchestrate.on_task_result` does. The post-processing chain (S5 stitch,
S7 tiles, S8 load-db, manifest) is the REAL code, running on the fakes' synthetic detections.
"""

from __future__ import annotations

import gzip
import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from cv_core.io.normalize import MediaError
from pipeline_fakes import NO_WIDE, Clock, DevFakeStages, FakeBackend, FakeRedis, MemoryStore, plan_every_120s
from pitchvision_schemas import Manifest, ShotType, Tile, keys
from pitchvision_worker import repository
from pitchvision_worker.compute.base import classify, success_key
from pitchvision_worker.compute.executor import execute
from pitchvision_worker.compute.router import BackendRouter
from pitchvision_worker.orchestrator import engine as eng
from pitchvision_worker.orchestrator.tasks import run_sweep
from pitchvision_worker.repository import org_tx
from sqlalchemy import Engine, create_engine, text

TAG = "sha-test"


@pytest.fixture
def db(pg_worker_url: str) -> Iterator[Engine]:
    e = create_engine(pg_worker_url)  # the orchestrator / workers' role (compose PV_WORKER_DATABASE_URL)
    yield e
    e.dispose()


@dataclass
class Harness:
    db: Engine
    org: uuid.UUID = field(default_factory=uuid.uuid4)
    video_id: uuid.UUID = field(default_factory=uuid.uuid4)
    job_id: uuid.UUID = field(default_factory=uuid.uuid4)
    store: MemoryStore = field(default_factory=MemoryStore)
    backend: FakeBackend = field(default_factory=FakeBackend)
    clock: Clock = field(default_factory=Clock)
    redis: FakeRedis = field(default_factory=FakeRedis)
    stages: DevFakeStages = field(default_factory=DevFakeStages)
    scheduled: list[float] = field(default_factory=list)
    pitch: bool = False  # Deps.pitch_available (a registered pitch keypoint model)

    def __post_init__(self) -> None:
        self.stages.db = self.db

    def deps(self) -> eng.Deps:
        router = BackendRouter({"local": self.backend}, ["local"], cpu=self.backend)
        return eng.Deps(engine=self.db, store=self.store, router=router, redis=self.redis,  # type: ignore[arg-type]
                        schedule=lambda j, o, d: self.scheduled.append(d), now=self.clock,
                        planners={"plan_segments": plan_every_120s}, max_inflight=16,
                        pitch_available=lambda: self.pitch)  # fmt: skip

    def setup(self, video_status: str = "ready", source_type: str = "upload") -> Harness:
        src = keys.source_key(str(self.org), str(self.video_id), "mp4")
        self.store.put_bytes(src, b"original-video")
        with org_tx(self.db, self.org) as c:
            c.execute(text("INSERT INTO videos (id, org_id, created_by, source_type, status, object_key) "
                           "VALUES (:v, :o, :u, :st, :s, :k)"),
                      {"v": self.video_id, "o": self.org, "u": uuid.uuid4(), "st": source_type, "s": video_status,
                       "k": src if video_status == "ready" else None})  # fmt: skip
            c.execute(text("INSERT INTO jobs (id, video_id, org_id, image_tag, config) VALUES (:j, :v, :o, :t, :c)"),
                      {"j": self.job_id, "v": self.video_id, "o": self.org, "t": TAG,
                       "c": json.dumps({"segment_target_s": 120})})  # fmt: skip
        return self

    def advance(self, report: eng.Report | None = None, deps: eng.Deps | None = None) -> str:
        return eng.advance(self.job_id, self.org, deps or self.deps(), report)

    def run_one(self, ext: str, task: Any, *, report: bool = True) -> None:
        try:
            result = execute(task, self.store, image_tag=TAG, stages=self.stages.registry())  # type: ignore[arg-type]
            rep = eng.Report(task.task_id, ext, True, result)
        except Exception as e:
            rep = eng.Report(task.task_id, ext, False, error=classify(e).as_dict())
        if report:
            self.advance(rep)

    def run_submitted(self, skip_stages: tuple[str, ...] = ()) -> int:
        """Run every submitted task (and the ones they lead to) until nothing is left; returns how many ran."""
        n, kept = 0, []
        while self.backend.submitted:
            ext, task = self.backend.submitted.pop(0)
            if task.stage in skip_stages:
                kept.append((ext, task))
                continue
            self.run_one(ext, task)
            n += 1
        self.backend.submitted.extend(kept)
        return n

    def sweep(self) -> None:
        """run_sweep drives EVERY active job in the shared test DB: keep only this job's submissions."""
        run_sweep(self.deps())
        self.backend.submitted = [(e, t) for e, t in self.backend.submitted if t.task_id.startswith(str(self.job_id))]

    def job(self) -> dict[str, Any]:
        with org_tx(self.db, self.org) as c:
            return dict(c.execute(text("SELECT * FROM jobs WHERE id = :j"), {"j": self.job_id}).mappings().one())

    def tasks(self) -> dict[str, dict[str, Any]]:
        with org_tx(self.db, self.org) as c:
            rows = c.execute(text("SELECT * FROM job_tasks WHERE job_id = :j"), {"j": self.job_id}).mappings()
            return {r["task_key"]: dict(r) for r in rows}

    def sql(self, stmt: str, **params: Any) -> list[Any]:
        with org_tx(self.db, self.org) as c:
            res = c.execute(text(stmt), {"j": self.job_id, "v": self.video_id, **params})
            return list(res) if res.returns_rows else []


@pytest.fixture
def h(db: Engine) -> Harness:
    return Harness(db)


def test_full_dag_to_done(h: Harness) -> None:
    h.setup()
    assert h.advance() == "calibrating"
    assert [t.stage for _, t in h.backend.submitted] == ["normalize"]
    h.run_submitted()
    job = h.job()
    assert job["status"] == "done" and float(job["progress"]) == 100 and job["finished_at"] and job["started_at"]
    assert job["coverage"] == {"main_wide_pct": 66.67} and job["error"] is None
    tasks = h.tasks()
    assert set(tasks) == {"normalize", "shots", "plan", "calibrate", "infer:000", "infer:001", "infer:002",
                          "infer:003", "stitch", "tiles", "load_db", "manifest"}  # fmt: skip
    assert all(t["status"] == "done" for t in tasks.values())
    assert tasks["plan"]["payload"]["pitch"] is False and not any(k.startswith("pitch:") for k in tasks)
    assert tasks["infer:002"]["attempts"] == 1 and tasks["normalize"]["payload"]["manifest"]["duration_ms"] == 600_000
    # S2b persisted: shots rows + media shots.json; S2 probe went into videos
    assert h.sql("SELECT count(*) FROM shots WHERE video_id = :v")[0][0] == 2
    org, vid, job_id = str(h.org), str(h.video_id), str(h.job_id)
    assert keys.shots_key(org, vid) in h.store.objects
    # S4 tasks carry their segment (no segments.json side channel)
    assert not any(k.endswith("segments.json") for k in h.store.objects)
    assert float(h.sql("SELECT fps FROM videos WHERE id = :v")[0][0]) == 50.0
    # outputs per contract
    assert keys.segment_prefix(org, vid, job_id, 3) + "detections.parquet" in h.store.objects
    assert success_key(keys.segment_prefix(org, vid, job_id, 3)) in h.store.objects
    assert keys.job_results_prefix(org, vid, job_id) + "models/team_model.json" in h.store.objects
    # events on job:{id}: progress … completed
    channels = {c for c, _ in h.redis.published}
    assert channels == {f"job:{h.job_id}"}
    types = [m["type"] for _, m in h.redis.published]
    assert types[-1] == "completed" and "progress" in types
    infer = [m["data"] for _, m in h.redis.published if m["type"] == "progress" and m["data"]["stage"] == "infer"]
    assert infer and infer[-1]["segments"]["total"] == 4
    assert {m["data"]["stage"] for _, m in h.redis.published if m["type"] == "progress"} >= {"stitch", "tiles"}

    # post-processing chain: tracks.parquet → 60 tiles + positions / teams / players → manifest (last)
    results = keys.job_results_prefix(org, vid, job_id)
    assert results + "tracks/tracks.parquet" in h.store.objects and success_key(results + "tracks/") in h.store.objects
    tile_keys = sorted(k for k in h.store.objects if k.startswith(keys.tile_prefix(org, vid, job_id, 0)))
    assert tile_keys == [keys.tile_key(org, vid, job_id, 0, n) for n in range(60)]
    tile = Tile.model_validate_json(gzip.decompress(h.store.objects[tile_keys[0]]))
    assert len(tile.frames) == 250 and tile.shots == [(0, 250, ShotType.main_wide)]
    assert sorted(o[0] for o in tile.frames[0].objs) == [-1, 1, 2]
    late = Tile.model_validate_json(gzip.decompress(h.store.objects[tile_keys[45]]))  # 450 s: close-up, not analysed
    assert late.shots == [(11250, 11500, ShotType.close_up)] and all(not f.objs for f in late.frames)
    manifest = Manifest.model_validate_json(h.store.objects[keys.manifest_key(org, vid)])
    assert (manifest.job_id, manifest.revision, manifest.tile_count, manifest.fps) == (job_id, 0, 60, 25)
    assert (manifest.width, manifest.height, manifest.duration_ms, manifest.frame_count) == (1280, 720, 600_000, 15000)
    assert manifest.tile_url_template == keys.tile_prefix(org, vid, job_id, 0) + "{tile_no}.json.gz"
    assert manifest.coverage.main_wide_pct == 66.67 and manifest.pitch.status == "unavailable"
    assert [(t.cluster, t.color) for t in manifest.teams] == [(0, "#c8102e"), (1, "#f5f5f5")]
    assert job["detail"]["pitch_calibration"] == "unavailable"
    assert tasks["manifest"]["payload"]["tile_count"] == 60
    # 5 Hz rows for 400 s of wide footage: 2 players + ball; the ids keep across the 120 s keyframe boundaries
    assert h.sql("SELECT count(*), count(DISTINCT track_id) FROM positions_org WHERE video_id = :v")[0] == (6000, 3)
    assert h.sql("SELECT count(*) FROM teams WHERE video_id = :v")[0][0] == 2
    assert h.sql("SELECT count(*) FROM players WHERE video_id = :v")[0][0] == 2
    assert h.advance() == "done"  # idempotent


def test_no_wide_shots_fails_job(h: Harness) -> None:
    h.setup()
    h.stages.timeline = NO_WIDE
    h.advance()
    h.run_submitted()
    job = h.job()
    assert job["status"] == "failed"
    assert job["error"]["code"] == "NO_WIDE_SHOTS" and job["error"]["retryable"] is False
    assert job["coverage"] == {"main_wide_pct": 0.0} and "calibrate" not in h.stages.calls
    assert h.redis.published[-1][1] == {"topic": f"job:{h.job_id}", "type": "failed",
                                        "data": {"code": "NO_WIDE_SHOTS", "retryable": False}}  # fmt: skip


def test_unreadable_video_is_not_retried(h: Harness) -> None:
    def bad() -> None:
        raise MediaError("UNREADABLE_MEDIA", "moov atom not found")

    h.setup()
    h.stages.fail["normalize"] = bad
    h.advance()
    h.run_submitted()
    job = h.job()
    assert (job["status"], job["error"]["code"], job["error"]["retryable"]) == ("failed", "VIDEO_UNREADABLE", False)
    assert h.stages.calls["normalize"] == 1


def test_team_separation_failure_from_calibrate(h: Harness) -> None:
    class TeamSeparationError(RuntimeError):
        code = "TEAM_SEPARATION_FAILED"

    def bad() -> None:
        raise TeamSeparationError("one colour cluster")

    h.setup()
    h.stages.fail["calibrate"] = bad
    h.advance()
    h.run_submitted()
    job = h.job()
    assert (job["status"], job["error"]["code"], job["error"]["retryable"]) == ("failed", "TEAM_SEPARATION_FAILED",
                                                                                 False)  # fmt: skip


def test_segment_retried_3_times_then_job_fails_and_retry_resumes(h: Harness) -> None:
    def boom() -> None:
        raise RuntimeError("CUDA OOM")

    h.setup()
    h.stages.fail["infer:001"] = boom
    h.advance()
    h.run_submitted()
    assert h.job()["status"] == "inferring"  # other segments finished, 001 backing off
    for delay in (30, 120, 300):
        assert h.scheduled[-1] == delay
        h.clock.tick(delay - 1)
        h.advance()
        assert h.backend.submitted == []  # not before the backoff
        h.clock.tick(1)
        h.advance()
        assert [t.task_id for _, t in h.backend.submitted] == [f"{h.job_id}:infer:001"]
        h.run_submitted()
    tasks = h.tasks()
    assert h.stages.calls["infer:001"] == 4 and tasks["infer:001"]["attempts"] == 4
    assert tasks["infer:001"]["status"] == "failed" and json.loads(tasks["infer:001"]["error"])["detail"]
    assert all(tasks[f"infer:00{i}"]["status"] == "done" for i in (0, 2, 3))
    job = h.job()
    assert job["status"] == "failed" and job["error"]["code"] == "INTERNAL" and job["error"]["retryable"] is True
    assert "1/4 segment(s) failed" in job["error"]["detail"]

    # POST /jobs/{id}/retry (what the API does): failed → queued, non-done tasks reset; done stages are skipped
    del h.stages.fail["infer:001"]
    h.sql("UPDATE jobs SET status = 'queued', error = NULL, finished_at = NULL WHERE id = :j")
    h.sql("UPDATE job_tasks SET status = 'queued', attempts = 0, error = NULL, external_id = NULL, not_before = NULL"
          " WHERE job_id = :j AND status <> 'done'")  # fmt: skip
    assert h.advance() == "inferring"
    assert [t.task_id for _, t in h.backend.submitted] == [f"{h.job_id}:infer:001"]
    h.run_submitted()
    assert h.job()["status"] == "done"
    assert h.stages.calls["normalize"] == 1 and h.stages.calls["calibrate"] == 1 and h.stages.calls["infer:000"] == 1


def test_checkpoint_skips_finished_stage(h: Harness) -> None:
    h.setup()
    h.advance()
    ext, task = h.backend.submitted.pop()
    h.run_one(ext, task, report=False)  # ran, but the worker died before reporting
    assert h.stages.calls["normalize"] == 1
    h.run_one(ext, task)  # acks_late redelivery: _SUCCESS exists → no work, same result
    assert h.stages.calls["normalize"] == 1 and h.tasks()["normalize"]["status"] == "done"
    assert [t.stage for _, t in h.backend.submitted] == ["shots"]


def test_image_tag_mismatch_refused(h: Harness) -> None:
    h.setup()
    h.advance()
    _, task = h.backend.submitted.pop()
    with pytest.raises(Exception, match="built for image") as exc:
        execute(task, h.store, image_tag="other", stages=h.stages.registry())  # type: ignore[arg-type]
    assert classify(exc.value).retryable is False and "normalize" not in h.stages.calls


def test_restart_recovery_from_job_tasks(h: Harness) -> None:
    h.setup()
    h.advance()
    ext, task = h.backend.submitted.pop()
    h.run_one(ext, task, report=False)  # result message lost (orchestrator was down)
    assert (h.job_id, h.org) in repository.active_jobs(h.db)  # SECURITY DEFINER: visible without app.org_id

    # "restart": fresh process state; the startup/periodic sweep resumes from job_tasks + _SUCCESS
    h.backend = FakeBackend()
    h.clock.tick(eng.POLL_AFTER_S + 1)
    h.sweep()
    assert h.tasks()["normalize"]["status"] == "done"
    assert [t.stage for _, t in h.backend.submitted] == ["shots"]

    # the shots task dies on the backend without writing _SUCCESS → counted as a failed attempt and retried
    ext, _ = h.backend.submitted.pop()
    h.backend.states[ext] = "failed"
    h.clock.tick(eng.POLL_AFTER_S + 1)
    h.sweep()
    row = h.tasks()["shots"]
    assert row["status"] == "queued" and row["attempts"] == 1 and row["not_before"] is not None
    h.clock.tick(30)
    h.sweep()
    assert [t.stage for _, t in h.backend.submitted] == ["shots"]
    h.run_submitted()
    assert h.job()["status"] == "done"
    assert (h.job_id, h.org) not in repository.active_jobs(h.db)


def test_cancel_revokes_dispatched_and_stops(h: Harness) -> None:
    h.setup()
    h.advance()
    h.run_submitted(skip_stages=("infer",))
    assert h.job()["status"] == "inferring" and len(h.backend.submitted) == 4
    exts = [ext for ext, _ in h.backend.submitted]
    h.sql("UPDATE jobs SET status = 'cancelled', finished_at = now() WHERE id = :j")  # POST /jobs/{id}/cancel
    assert (h.job_id, h.org) in repository.active_jobs(h.db)  # still has tasks to revoke
    h.backend.submitted.clear()
    h.sweep()
    assert set(exts) <= set(h.backend.cancelled) and h.backend.submitted == []
    assert all(r["status"] == "failed" for k, r in h.tasks().items() if k.startswith("infer:"))
    assert (h.job_id, h.org) not in repository.active_jobs(h.db)
    assert h.advance() == "cancelled" and h.backend.submitted == []
    # the sweep never announces; orchestrate.cancel_job (API cancel / video delete) publishes `cancelled` once
    assert "cancelled" not in [m["type"] for _, m in h.redis.published]
    assert eng.advance(h.job_id, h.org, h.deps(), announce_cancel=True) == "cancelled"
    assert h.redis.published[-1][1] == {"topic": f"job:{h.job_id}", "type": "cancelled",
                                        "data": {"video_id": str(h.video_id)}}  # fmt: skip


def test_infer_tasks_carry_their_segment(h: Harness) -> None:
    h.setup()
    h.advance()
    h.run_submitted(skip_stages=("infer",))
    infer = sorted((t for _, t in h.backend.submitted), key=lambda t: t.task_id)
    assert [t.segment.index for t in infer] == [0, 1, 2, 3]  # type: ignore[union-attr]
    assert [(t.segment.t0_ms, t.segment.t1_ms) for t in infer][:2] == [(0, 120_000), (120_000, 240_000)]  # type: ignore[union-attr]
    assert all(set(t.inputs) == {"normalized", "team_model"} for t in infer)
    payload = infer[0].model_dump(mode="json")  # what LocalBackend ships to gpu.run_stage
    assert type(infer[0]).model_validate(payload).segment == infer[0].segment


def test_youtube_job_waits_for_ingest(h: Harness) -> None:
    h.setup(video_status="ingesting", source_type="youtube")
    assert h.advance() == "ingesting" and h.backend.submitted == []
    assert h.job()["stage"] == "ingest"
    src = keys.source_key(str(h.org), str(h.video_id), "mp4")
    h.sql("UPDATE videos SET status = 'ready', object_key = :k WHERE id = :v", k=src)
    assert h.advance() == "calibrating"  # what orchestrate.video_ready does
    assert [t.inputs["source"] for _, t in h.backend.submitted] == [src]


def test_ingest_failure_fails_waiting_job(h: Harness) -> None:
    h.setup(video_status="ingesting", source_type="youtube")
    h.advance()
    h.sql("UPDATE videos SET status = 'failed', error = '{\"code\": \"INGEST_FAILED\", \"reason\": \"private\"}' "
          "WHERE id = :v")  # fmt: skip
    assert h.advance() == "failed"
    assert h.job()["error"] == {"code": "INGEST_FAILED", "retryable": False, "detail": "private"}


def test_other_org_cannot_drive_job(h: Harness) -> None:
    h.setup()
    assert eng.advance(h.job_id, uuid.uuid4(), h.deps()) == "missing"  # RLS
    assert repository.job_org(h.db, h.job_id) == h.org


# ---------------------------------------------------------------- W4: optional pitch stage + post-processing chain
def test_pitch_stage_projects_positions(h: Harness) -> None:
    h.pitch = True
    h.setup()
    h.advance()
    h.run_submitted()
    tasks = h.tasks()
    assert tasks["plan"]["payload"]["pitch"] is True
    assert all(tasks[f"pitch:00{i}"]["status"] == "done" for i in range(4))
    assert h.stages.calls["pitch:002"] == 1
    org, vid, job_id = str(h.org), str(h.video_id), str(h.job_id)
    assert keys.segment_prefix(org, vid, job_id, 2) + "pitch/homography.parquet" in h.store.objects
    assert success_key(keys.segment_prefix(org, vid, job_id, 2) + "pitch/") in h.store.objects
    job = h.job()
    assert job["status"] == "done" and job["detail"]["pitch_calibration"] == "ok"
    m = Manifest.model_validate_json(h.store.objects[keys.manifest_key(org, vid)])
    assert m.pitch.status == "ok" and m.coverage.homography_ok_pct == 100.0
    xy = h.sql("SELECT count(*), count(px), min(px), max(px) FROM positions_org WHERE video_id = :v")[0]
    assert xy[0] == xy[1] == 6000 and 5 < xy[2] < xy[3] < 60
    tile = Tile.model_validate_json(gzip.decompress(h.store.objects[keys.tile_key(org, vid, job_id, 0, 1)]))
    assert tile.frames[0].hq == pytest.approx(0.9) and all(o[8] is not None for o in tile.frames[0].objs)


def test_failed_pitch_segments_degrade_but_never_fail_the_job(h: Harness) -> None:
    def broken() -> None:
        raise ValueError("bad keypoints")  # INTERNAL, retryable → retried, then given up

    h.pitch = True
    h.setup()
    h.stages.fail["pitch:001"] = broken
    h.stages.fail["pitch:002"] = broken
    h.advance()
    h.run_submitted()
    for _ in range(eng.MAX_RETRIES):
        h.clock.tick(300)
        h.advance()
        h.run_submitted()
    tasks = h.tasks()
    assert tasks["pitch:001"]["status"] == tasks["pitch:002"]["status"] == "failed"
    assert h.job()["status"] == "done" and h.job()["detail"]["pitch_calibration"] == "degraded"  # 40 % wide w/o H
    m = Manifest.model_validate_json(h.store.objects[keys.manifest_key(str(h.org), str(h.video_id))])
    assert m.pitch.status == "degraded" and m.coverage.homography_ok_pct == 40.0


def test_post_processing_order_and_manifest_last(h: Harness) -> None:
    h.setup()
    h.advance()
    h.run_submitted(skip_stages=("tiles", "load_db"))
    job = h.job()
    assert job["status"] == "postprocessing" and job["stage"] == "tiles"
    pending = sorted(t.stage for _, t in h.backend.submitted)
    assert pending == ["load_db", "tiles"]  # dispatched together once stitch is done
    tiles_task = next(t for _, t in h.backend.submitted if t.stage == "tiles")
    assert tiles_task.params["revision"] == 0 and tiles_task.params["duration_ms"] == 600_000  # type: ignore[union-attr]
    assert keys.manifest_key(str(h.org), str(h.video_id)) not in h.store.objects
    h.run_submitted(skip_stages=("tiles",))  # load_db done, tiles still running → no manifest yet
    assert h.job()["status"] == "postprocessing" and h.job()["stage"] == "tiles"
    assert keys.manifest_key(str(h.org), str(h.video_id)) not in h.store.objects
    h.run_submitted()
    assert h.job()["status"] == "done" and keys.manifest_key(str(h.org), str(h.video_id)) in h.store.objects
    assert h.tasks()["manifest"]["status"] == "done"


def test_post_stage_failure_fails_job_and_retry_reuses_tracks(h: Harness) -> None:
    from pitchvision_schemas import ErrorCode
    from pitchvision_worker.compute.base import StageFailure
    from pitchvision_worker.compute.stages import STAGES

    h.setup()
    real_tiles = STAGES["tiles"]

    def bad_tiles(*a: Any) -> Any:
        raise StageFailure(ErrorCode.INTERNAL, "disk full", retryable=False)

    registry = h.stages.registry
    h.stages.registry = lambda: {**registry(), "tiles": bad_tiles}  # type: ignore[method-assign]
    h.advance()
    h.run_submitted()
    job = h.job()
    assert job["status"] == "failed" and job["stage"] == "tiles" and job["error"]["detail"] == "disk full"
    assert keys.manifest_key(str(h.org), str(h.video_id)) not in h.store.objects
    # retry (API): stitch is done and checkpointed → only tiles (and the manifest) run again
    h.stages.registry = lambda: {**registry(), "tiles": real_tiles}  # type: ignore[method-assign]
    h.sql("UPDATE jobs SET status = 'queued', error = NULL, finished_at = NULL WHERE id = :j")
    h.sql("UPDATE job_tasks SET status = 'queued', attempts = 0, error = NULL, external_id = NULL, not_before = NULL"
          " WHERE job_id = :j AND status <> 'done'")  # fmt: skip
    h.advance()
    assert [t.stage for _, t in h.backend.submitted] == ["tiles"]
    h.run_submitted()
    assert h.job()["status"] == "done" and h.stages.calls["infer:000"] == 1
