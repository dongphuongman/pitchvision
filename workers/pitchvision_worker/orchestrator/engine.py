"""Job DAG driver (AD §6.1, §8.2, §16; docs/contracts/pipeline-stages.md).

``advance(job_id, org_id)`` is ONE idempotent step function; everything that can change a job calls it:
``start_job``, every stage result (``on_task_result``), retry-backoff timers, the ingest worker when a YouTube video
becomes ready, and the periodic/startup ``sweep``. Each call opens one RLS-scoped transaction, locks the job row
(``FOR UPDATE`` — concurrent calls serialise, so nothing is dispatched twice), reloads ``job_tasks`` and moves the
job as far as it can without waiting:

    queued → ingesting (wait for the video to be ready) → calibrating: normalize (cpu) → shots (gpu) → plan (inline)
    → calibrate (gpu) → inferring: infer:NNN (+ optional pitch:NNN) fan-out (gpu, ≤ max_inflight each)
    → postprocessing: stitch (cpu) → tiles + load_db (cpu, parallel) → manifest (inline, LAST) → done

Optional pitch calibration (M1-CV-11/12): decided once at plan time (``Deps.pitch_available``: the image's model
registry has a ``pitch_keypoints`` entry) and stored in the plan payload. A pitch task that fails or reports
``unavailable`` never fails the job; S5 simply gets no homography for that segment. The verdict (ok / degraded /
unavailable) comes from S5 and is written to ``jobs.detail.pitch_calibration``.

Derived jobs (``jobs.detail.kind``, pitchvision_schemas.JobKind; created by the API, docs/contracts/pipeline-stages.md):
- ``team_rerun`` (SCR-14 manual teams): normalize / shots / plan are INHERITED from ``detail.parent_job_id`` (their
  job_tasks rows are copied as done, ``backend = "inherited"``, ``external_id`` = the job that produced the objects,
  and ``_prefix`` reads that job's objects); calibrate runs with input ``seeds`` (``detail.team_seeds`` written to
  ``results/…/jobs/{job}/inputs/team_seeds.json``), then everything else runs as usual.
- ``recalibrate`` (SCR-15 manual pitch calibration): normalize / shots / plan / calibrate are inherited, S4 is skipped
  and the CPU task ``recompute`` (``stages.recompute``) replaces S5: parent's tracks.parquet + ``detail.calibration``
  → this job's tracks.parquet; then tiles (under the video's current ``revision``, bumped by the API), load_db and
  the manifest as usual. ``detail.pitch_calibration`` becomes ``manual``.
The origin map is persisted as ``detail.inherit`` ({task_key: job_id}); chains resolve to the job that really ran it.

Completion tracking (robust to worker AND orchestrator restarts — no chord, no in-memory state):
- job_tasks is the durable state; a row is pending (status queued, no external_id), dispatched (running +
  backend/external_id), done (payload = result) or failed (error JSON).
- fast path: the executor sends ``orchestrate.on_task_result``; ``advance(report=…)`` records it first.
- slow path: for a dispatched row older than POLL_AFTER_S, the step checks the stage's ``_SUCCESS`` object (its body
  is the result) and ``ComputeBackend.status``; a lost/expired task is failed as an attempt and retried.
- retries: 3 retries per task, backoff 30 s / 2 min / 5 min (``not_before`` + a delayed advance); a failing segment
  never blocks the others — the job fails only once every segment is terminal.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, NoReturn

from pitchvision_schemas import (
    CpuTask,
    ErrorCode,
    GpuTask,
    JobStatus,
    PipelineConfig,
    Segment,
    ShotTimeline,
    TaskHandle,
    keys,
)
from pitchvision_schemas.jobs import can_transition
from sqlalchemy import Connection, Engine

from pitchvision_worker import events
from pitchvision_worker.compute.base import StageFailure, StageTask, classify, success_key
from pitchvision_worker.compute.router import BackendRouter
from pitchvision_worker.compute.stages import (
    PLANNERS,
    SHOTS_FILE,
    SegmentPlanner,
    detections_input,
    homography_input,
)
from pitchvision_worker.orchestrator import store
from pitchvision_worker.orchestrator.manifest import build_manifest
from pitchvision_worker.repository import org_tx
from pitchvision_worker.storage import ObjectStore

log = logging.getLogger(__name__)

NORMALIZE, SHOTS, PLAN, CALIBRATE = "normalize", "shots", "plan", "calibrate"
STITCH, TILES, LOAD_DB, MANIFEST = "stitch", "tiles", "load_db", "manifest"
RECOMPUTE = "recompute"  # recalibrate jobs: manual-H re-projection in place of S5
INHERITED = {"team_rerun": (NORMALIZE, SHOTS, PLAN), "recalibrate": (NORMALIZE, SHOTS, PLAN, CALIBRATE)}
DETAIL_KEEP = ("kind", "parent_job_id", "inherit", "team_seeds", "calibration", "correction_id")  # set by the API
SEEDS_FILE = "team_seeds.json"
POST_STAGES = (STITCH, TILES, LOAD_DB, MANIFEST)  # S5, S7, S8 (cpu tasks) + the manifest (inline, last)
DETECTIONS_FILE = "detections.parquet"  # S4 output under keys.segment_prefix (contract)
HOMOGRAPHY_FILE = "homography.parquet"  # pitch output under keys.segment_prefix + "pitch/"
TRACKS_FILE = "tracks.parquet"  # S5 output under results/…/jobs/{job}/tracks/
TEAM_MODEL_FILE = "team_model.json"
BACKOFF_S = (30, 120, 300)  # AD §16.1: retry a task 3 times, then it fails
MAX_RETRIES = len(BACKOFF_S)
POLL_AFTER_S = 60  # check backend/_SUCCESS for dispatched tasks silent for this long (the result message is faster)
STALE_QUEUED_S = 6 * 3600  # = broker visibility timeout: a message still PENDING after this is considered lost
RUNNING_GRACE_S = 600
TIMEOUT_S = {NORMALIZE: 3 * 3600, SHOTS: 2 * 3600, CALIBRATE: 1800, "infer": 1800, "pitch": 1800, STITCH: 1800,
             TILES: 1800, LOAD_DB: 1800, RECOMPUTE: 1800}  # fmt: skip
WEIGHTS = {NORMALIZE: 5.0, SHOTS: 5.0, CALIBRATE: 5.0, "infer": 80.0, "post": 5.0}  # AD §8.2: S4 is 80 %
TERMINAL = {JobStatus.done, JobStatus.failed, JobStatus.cancelled}


def infer_key(i: int) -> str:
    return f"infer:{i:03d}"


def pitch_key(i: int) -> str:
    return f"pitch:{i:03d}"


def task_key_of(task_id: str) -> tuple[uuid.UUID, str]:
    """GpuTask.make_id(job, stage, seg) → (job_id, job_tasks.task_key)."""
    job, stage, *seg = task_id.split(":")
    return uuid.UUID(job), (f"{stage}:{int(seg[0]):03d}" if stage in ("infer", "pitch") and seg else stage)


def pitch_model_available() -> bool:
    """Default ``Deps.pitch_available``: the model registry in this image lists a pitch keypoint model."""
    from pitchvision_worker.stages.pitch import pitch_model_registered

    return pitch_model_registered()


@dataclass
class Report:
    """A stage result delivered by the executor (orchestrate.on_task_result)."""

    task_id: str
    external_id: str | None
    ok: bool
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


@dataclass
class Deps:
    engine: Engine
    store: ObjectStore
    router: BackendRouter
    redis: Any | None = None
    schedule: Callable[[uuid.UUID, uuid.UUID, float], None] = lambda job, org, delay: None
    max_inflight: int = 16
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    planners: Mapping[str, SegmentPlanner] = field(default_factory=lambda: PLANNERS)
    pitch_available: Callable[[], bool] = pitch_model_available


def advance(job_id: uuid.UUID, org_id: uuid.UUID, deps: Deps, report: Report | None = None, *,
            announce_cancel: bool = False) -> str:  # fmt: skip
    """One step; returns the job status afterwards ("missing" if the job isn't visible to this org).

    ``announce_cancel`` (``orchestrate.cancel_job``, sent by the API's cancel / video-delete path): publish the WS
    ``cancelled`` event once the job is seen cancelled. The sweep never announces, so the event is sent once per
    cancel request (subscribers that join later get ``subscribed {status: cancelled}`` + a cancelled snapshot).
    """
    with org_tx(deps.engine, org_id) as conn:
        job = store.lock_job(conn, job_id)
        if job is None:
            return "missing"
        run = _Run(conn, job, deps)
        if report is not None:
            run.record(report)
        run.drive()
        if announce_cancel and run.status == JobStatus.cancelled:
            run.events.append(events.cancelled(run.id, run.video_id))
    for ev in run.events:  # after commit: a client never sees an event for state that rolled back
        events.publish(deps.redis, ev)
    if run.wake_in is not None:
        deps.schedule(job_id, org_id, run.wake_in)
    return run.status.value


class _JobFailed(Exception):
    pass


class _Run:
    def __init__(self, conn: Connection, job: store.Row, deps: Deps) -> None:
        self.conn, self.job, self.deps = conn, job, deps
        self.id: uuid.UUID = job["id"]
        self.org: uuid.UUID = job["org_id"]
        self.video_id: uuid.UUID = job["video_id"]
        self.status = JobStatus(job["status"])
        self.config = PipelineConfig.model_validate(job["config"])
        self.tasks = store.load_tasks(conn, self.id)
        self.now = deps.now()
        self.stage: str | None = job["stage"]
        self.events: list[Any] = []
        self.wake_in: float | None = None
        self.results = keys.job_results_prefix(str(self.org), str(self.video_id), str(self.id))
        self.detail: dict[str, Any] = dict(job["detail"] or {})
        self.kind: str = str(self.detail.get("kind") or "analyze")
        self.inherit: dict[str, str] = {str(k): str(v) for k, v in (self.detail.get("inherit") or {}).items()}

    # ------------------------------------------------------------------ driving
    def drive(self) -> None:
        if self.status == JobStatus.cancelled:
            self._revoke_inflight()
            return
        if self.status in TERMINAL:
            return
        try:
            if self.kind in INHERITED and "inherit" not in self.detail:
                self._inherit_from_parent()
            self._poll_inflight()
            if self.status == JobStatus.queued:
                self._to(JobStatus.ingesting)
            if self.status == JobStatus.ingesting:
                if not self._source_ready():
                    return
                self._to(JobStatus.calibrating)
            if self.status == JobStatus.calibrating:
                if not self._calibrating():
                    return
                self._to(JobStatus.inferring)
            if self.status == JobStatus.inferring:
                if not self._inferring():
                    return
                self._to(JobStatus.postprocessing)
            if self.status == JobStatus.postprocessing:
                if not self._postprocess():
                    return
                self._to(JobStatus.done)
                store.update_job(self.conn, self.id, finished_at=self.now, error=None)
                self.events.append(events.completed(self.id, self.video_id))
        except _JobFailed:
            pass
        finally:
            self._progress()

    def _to(self, nxt: JobStatus) -> None:
        if not can_transition(self.status, nxt):
            raise RuntimeError(f"job {self.id}: illegal transition {self.status} → {nxt}")
        values: dict[str, Any] = {"status": nxt.value}
        if self.status == JobStatus.queued and self.job["started_at"] is None:
            values["started_at"] = self.now
        store.update_job(self.conn, self.id, **values)
        log.info("job %s: %s → %s", self.id, self.status.value, nxt.value)
        self.status = nxt

    def _fail_job(self, failure: StageFailure) -> NoReturn:
        if can_transition(self.status, JobStatus.failed):
            self._to(JobStatus.failed)
            store.update_job(self.conn, self.id, error=failure.as_dict(), finished_at=self.now)
            self.events.append(events.failed(self.id, failure.code, failure.retryable))
            log.warning("job %s failed: %s", self.id, failure)
        raise _JobFailed

    # ------------------------------------------------------------------ derived jobs
    def _parent_id(self) -> uuid.UUID | None:
        raw = self.detail.get("parent_job_id")
        try:
            return uuid.UUID(str(raw)) if raw else None
        except ValueError:
            return None

    def _inherit_from_parent(self) -> None:
        """Copy the parent's finished stages this kind reuses (once; recorded in ``detail.inherit``)."""
        parent_id = self._parent_id()
        parent = store.get_job(self.conn, parent_id) if parent_id else None
        if parent is None or parent_id is None:
            self._fail_job(StageFailure(ErrorCode.INTERNAL, f"{self.kind} job without a readable parent job",
                                        retryable=False))  # fmt: skip
        ptasks = store.load_tasks(self.conn, parent_id)
        porigin = {str(k): str(v) for k, v in ((parent["detail"] or {}).get("inherit") or {}).items()}
        for key in INHERITED[self.kind]:
            prow = ptasks.get(key)
            if prow is None or prow["status"] != "done" or key in self.tasks:
                continue
            origin = porigin.get(key, str(parent_id))
            row = self._insert(key)
            store.update_task(self.conn, row, status="done", payload=prow.get("payload"), error=None,
                              backend="inherited", external_id=origin, finished_at=self.now)  # fmt: skip
            self.inherit[key] = origin
        if self.kind == "recalibrate":
            missing = [k for k in INHERITED["recalibrate"] if k not in self.inherit]
            if missing or parent["status"] != JobStatus.done.value:
                self._fail_job(StageFailure(ErrorCode.INTERNAL, f"parent job {parent_id} is {parent['status']} / "
                                            f"lacks {missing}: nothing to recalibrate", retryable=False))  # fmt: skip
        self.detail["inherit"] = self.inherit
        store.update_job(self.conn, self.id, detail=self.detail)
        log.info("job %s (%s): inherited %s from %s", self.id, self.kind, sorted(self.inherit), parent_id)

    def _tracks_task(self) -> str:
        """The post stage producing this job's tracks.parquet: S5 stitch, or the manual-H recompute."""
        return RECOMPUTE if self.kind == "recalibrate" else STITCH

    def _post_stages(self) -> tuple[str, ...]:
        return (self._tracks_task(), TILES, LOAD_DB, MANIFEST)

    # ------------------------------------------------------------------ stages
    def _source_ready(self) -> bool:
        self.stage = "ingest"
        video = store.get_video(self.conn, self.video_id)
        if video is None or video["deleted_at"] is not None or video["status"] == "deleted":
            self._fail_job(StageFailure(ErrorCode.VIDEO_UNREADABLE, "video was deleted", retryable=False))
        if video["status"] == "failed":
            reason = (video.get("error") or {}).get("reason") or "ingest failed"
            self._fail_job(StageFailure(ErrorCode.INGEST_FAILED, str(reason), retryable=False))
        if video["status"] in ("uploading", "ingesting"):
            return False  # the ingest worker pokes us (orchestrate.video_ready); the sweep is the fallback
        if not video["object_key"]:
            self._fail_job(StageFailure(ErrorCode.VIDEO_UNREADABLE, "video has no source object", retryable=False))
        return True

    def _source_key(self) -> str:
        video = store.get_video(self.conn, self.video_id) or {}
        return str(video.get("object_key") or "")

    def _calibrating(self) -> bool:
        for key in (NORMALIZE, SHOTS):
            self.stage = key
            if not self._ensure_done(key):
                return False
        self.stage = PLAN
        if not self._plan():
            return False
        self.stage = CALIBRATE
        return self._ensure_done(CALIBRATE)

    def _ensure_done(self, key: str) -> bool:
        state = self._ensure(key)
        if state == "failed":
            self._fail_job(self._task_failure(key))
        return state == "done"

    def _plan(self) -> bool:
        row = self.tasks.get(PLAN) or self._insert(PLAN)
        if row["status"] == "done":
            return True
        timeline = ShotTimeline.model_validate_json(self.deps.store.get_bytes(self._prefix(SHOTS) + SHOTS_FILE))
        n = store.replace_shots(self.conn, self.org, self.video_id, timeline)
        self.deps.store.copy(self._prefix(SHOTS) + SHOTS_FILE, keys.shots_key(str(self.org), str(self.video_id)))
        pct = round(100 * timeline.coverage(), 2)
        store.update_job(self.conn, self.id, coverage={"main_wide_pct": pct})
        if pct <= 0:
            self._task_failed(row, StageFailure(ErrorCode.NO_WIDE_SHOTS, "no main_wide shots", retryable=False))
            self._fail_job(self._task_failure(PLAN))
        duration_ms = int(((self.tasks[NORMALIZE].get("payload") or {}).get("manifest") or {}).get("duration_ms")
                          or timeline.shots[-1].t1_ms)  # fmt: skip
        try:
            segments = self.deps.planners["plan_segments"](timeline, duration_ms, self.config)
        except Exception as e:
            self._task_failed(row, classify(e))
            self._fail_job(self._task_failure(PLAN))
        if not segments:
            self._task_failed(row, StageFailure(ErrorCode.NO_WIDE_SHOTS, "no analysable segment", retryable=False))
            self._fail_job(self._task_failure(PLAN))
        try:
            pitch = bool(self.deps.pitch_available())
        except Exception as e:  # optional stage: never fail the plan over it
            log.warning("job %s: pitch availability check failed: %r", self.id, e)
            pitch = False
        plan = {"segments": [s.model_dump() for s in segments], "main_wide_pct": pct, "shots": n, "pitch": pitch}
        self._task_done(row, plan)
        log.info("job %s: %d shots, main_wide %.1f%%, %d segment(s), pitch stage %s", self.id, n, pct,
                 len(segments), "on" if pitch else "unavailable")  # fmt: skip
        return True

    def _segments(self) -> list[dict[str, Any]]:
        segs: list[dict[str, Any]] = (self.tasks.get(PLAN, {}).get("payload") or {}).get("segments") or []
        return segs

    def _pitch_enabled(self) -> bool:
        return bool((self.tasks.get(PLAN, {}).get("payload") or {}).get("pitch"))

    def _inferring(self) -> bool:
        self.stage = "infer"
        if self.kind == "recalibrate":  # detections are reused through the parent's tracks.parquet
            return True
        states = [self._ensure(infer_key(s["index"])) for s in self._segments()]
        pitch_states = [self._ensure(pitch_key(s["index"])) for s in self._segments()] if self._pitch_enabled() else []
        if any(s == "waiting" for s in (*states, *pitch_states)):
            return False
        failed = [infer_key(s["index"]) for s, st in zip(self._segments(), states, strict=True) if st == "failed"]
        if failed:
            first = self._task_failure(failed[0])
            detail = f"{len(failed)}/{len(states)} segment(s) failed; first ({failed[0]}): {first.detail}"
            self._fail_job(StageFailure(first.code, detail, first.retryable))
        lost = [k for k in (pitch_key(s["index"]) for s in self._segments()) if self.tasks.get(k, {}).get("status")
                == "failed"]  # fmt: skip
        if lost:  # optional stage: those segments simply get no homography
            log.warning("job %s: pitch failed for %d segment(s) (%s); continuing without them", self.id, len(lost),
                        ", ".join(lost))  # fmt: skip
        return True

    def _postprocess(self) -> bool:
        """stitch (or recompute) → tiles + load_db → manifest. True once the manifest is written (job can be done)."""
        self.stage = self._tracks_task()
        if not self._ensure_done(self._tracks_task()):
            return False
        states = {k: self._ensure(k) for k in (TILES, LOAD_DB)}
        for key, state in states.items():
            if state == "failed":
                self.stage = key
                self._fail_job(self._task_failure(key))
        waiting = [k for k, s in states.items() if s == "waiting"]
        if waiting:
            self.stage = waiting[0]
            return False
        self.stage = MANIFEST
        self._write_manifest()
        return True

    def _media(self) -> dict[str, Any]:
        media: dict[str, Any] = (self.tasks.get(NORMALIZE, {}).get("payload") or {}).get("manifest") or {}
        return media

    def _timeline(self) -> ShotTimeline:
        return ShotTimeline.model_validate_json(self.deps.store.get_bytes(self._prefix(SHOTS) + SHOTS_FILE))

    def _write_manifest(self) -> None:
        """LAST step before done: manifest.json at keys.manifest_key (tiles + DB rows already exist)."""
        row = self.tasks.get(MANIFEST) or self._insert(MANIFEST)
        if row["status"] == "done":
            return
        try:
            team_model = json.loads(self.deps.store.get_bytes(self._prefix(CALIBRATE) + TEAM_MODEL_FILE))
        except Exception as e:  # display defaults (names / palette colours) are fine without it
            log.warning("job %s: team model unreadable for the manifest: %r", self.id, e)
            team_model = None
        manifest = build_manifest(org_id=self.org, video_id=self.video_id, job_id=self.id, config=self.config,
                                  media=self._media(), timeline=self._timeline(),
                                  team_model=team_model if isinstance(team_model, dict) else None,
                                  stitch=self.tasks[self._tracks_task()].get("payload") or {},
                                  tiles=self.tasks[TILES].get("payload") or {})  # fmt: skip
        key = keys.manifest_key(str(self.org), str(self.video_id))
        self.deps.store.put_bytes(key, manifest.model_dump_json().encode())
        self._task_done(row, {"key": key, "tile_count": manifest.tile_count, "revision": manifest.revision,
                              "pitch": manifest.pitch.status})  # fmt: skip
        log.info("job %s: manifest written (%d tiles, revision %d, pitch %s)", self.id, manifest.tile_count,
                 manifest.revision, manifest.pitch.status)  # fmt: skip

    def _pitch_status(self) -> str | None:
        """S5's (or recompute's) pitch verdict once it is done (→ jobs.detail.pitch_calibration, read by the API)."""
        row = self.tasks.get(self._tracks_task())
        if row and row["status"] == "done":
            status = ((row.get("payload") or {}).get("pitch") or {}).get("status")
            return str(status) if status else None
        prev = (self.job["detail"] or {}).get("pitch_calibration")
        return str(prev) if prev else None

    # ------------------------------------------------------------------ tasks
    def _insert(self, key: str) -> store.Row:
        row = store.insert_task(self.conn, self.id, self.org, key)
        self.tasks[key] = row
        return row

    def _ensure(self, key: str) -> str:
        """done | failed | waiting — dispatches the task when it is pending and allowed to run now."""
        row = self.tasks.get(key) or self._insert(key)
        if row["status"] in ("done", "failed"):
            return str(row["status"])
        if row["status"] == "running":
            return "waiting"
        if row["not_before"] is not None and row["not_before"] > self.now:
            self._wake((row["not_before"] - self.now).total_seconds())
            return "waiting"
        for fan_out in ("infer:", "pitch:"):
            if key.startswith(fan_out) and self._inflight(fan_out) >= self.deps.max_inflight:
                return "waiting"
        self._dispatch(row, self._build(key))
        return str(row["status"]) if row["status"] in ("done", "failed") else "waiting"

    def _inflight(self, prefix: str) -> int:
        return sum(1 for k, r in self.tasks.items() if k.startswith(prefix) and r["status"] == "running")

    def _dispatch(self, row: store.Row, task: StageTask) -> None:
        if self.deps.store.exists(success_key(task.output_prefix)):  # checkpoint from an earlier run / lost message
            self._task_done(row, json.loads(self.deps.store.get_bytes(success_key(task.output_prefix)) or b"{}"))
            return
        try:
            backend = self.deps.router.for_task(task)
            handle = backend.submit(task)
        except NotImplementedError as e:
            store.update_task(self.conn, row, attempts=row["attempts"] + 1)
            self._task_failed(row, StageFailure(ErrorCode.INTERNAL, f"backend not implemented: {e}", False))
            return
        store.update_task(self.conn, row, status="running", backend=handle.backend, external_id=handle.external_id,
                          attempts=row["attempts"] + 1, dispatched_at=self.now, not_before=None,
                          started_at=row["started_at"] or self.now)  # fmt: skip
        log.info("job %s: dispatched %s → %s %s (attempt %d)", self.id, row["task_key"], handle.backend,
                 handle.external_id, row["attempts"])  # fmt: skip

    def _build(self, key: str) -> StageTask:
        org, vid, job = str(self.org), str(self.video_id), str(self.id)
        normalized = keys.normalized_key(org, vid)
        common: dict[str, Any] = {"image_tag": self.job["image_tag"], "config": self.config}
        if key == NORMALIZE:
            return CpuTask(task_id=CpuTask.make_id(job, NORMALIZE), stage="normalize", output_prefix=self._prefix(key),
                           inputs={"source": self._source_key()}, outputs={"normalized": normalized},
                           timeout_s=TIMEOUT_S[NORMALIZE], **common)  # fmt: skip
        if key == SHOTS:
            return GpuTask(task_id=GpuTask.make_id(job, SHOTS), stage="shots", output_prefix=self._prefix(key),
                           inputs={"normalized": normalized}, timeout_s=TIMEOUT_S[SHOTS], **common)  # fmt: skip
        if key == CALIBRATE:
            inputs = {"normalized": normalized, "shots": self._prefix(SHOTS) + SHOTS_FILE}
            if self.kind == "team_rerun" and self.detail.get("team_seeds"):
                inputs["seeds"] = self.results + "inputs/" + SEEDS_FILE
                self.deps.store.put_bytes(inputs["seeds"], json.dumps(self.detail["team_seeds"]).encode())
            return GpuTask(task_id=GpuTask.make_id(job, CALIBRATE), stage="calibrate", output_prefix=self._prefix(key),
                           inputs=inputs, timeout_s=TIMEOUT_S[CALIBRATE], **common)  # fmt: skip
        if key in (STITCH, TILES, LOAD_DB, RECOMPUTE):
            return self._build_post(key, common)
        index = int(key.split(":")[1])
        if key.startswith("pitch:"):
            return GpuTask(task_id=GpuTask.make_id(job, "pitch", index), stage="pitch",
                           output_prefix=self._prefix(key), segment=self._segment(index), timeout_s=TIMEOUT_S["pitch"],
                           inputs={"normalized": normalized, "shots": self._prefix(SHOTS) + SHOTS_FILE},
                           **common)  # fmt: skip
        inputs = {"normalized": normalized, "team_model": self._prefix(CALIBRATE) + TEAM_MODEL_FILE}
        return GpuTask(task_id=GpuTask.make_id(job, "infer", index), stage="infer", output_prefix=self._prefix(key),
                       inputs=inputs, segment=self._segment(index), timeout_s=TIMEOUT_S["infer"],
                       **common)  # fmt: skip

    def _build_post(self, key: str, common: dict[str, Any]) -> CpuTask:
        """S5 / S7 / S8 CpuTasks: object keys in ``inputs``, ids / revision / plan in ``params``."""
        org, vid, job = str(self.org), str(self.video_id), str(self.id)
        shots = self._prefix(SHOTS) + SHOTS_FILE
        tracks = self._prefix(STITCH) + TRACKS_FILE
        ids = {"org_id": org, "video_id": vid, "job_id": job}
        if key == STITCH:
            inputs = {"shots": shots}
            for s in self._segments():
                i = int(s["index"])
                inputs[detections_input(i)] = self._prefix(infer_key(i)) + DETECTIONS_FILE
                pitch = self.tasks.get(pitch_key(i))
                if pitch and pitch["status"] == "done" and (pitch.get("payload") or {}).get("status") == "ok":
                    inputs[homography_input(i)] = self._prefix(pitch_key(i)) + HOMOGRAPHY_FILE
            return CpuTask(task_id=CpuTask.make_id(job, STITCH), stage="stitch", output_prefix=self._prefix(key),
                           inputs=inputs, params={**ids, "segments": self._segments()}, timeout_s=TIMEOUT_S[STITCH],
                           **common)  # fmt: skip
        if key == RECOMPUTE:
            parent = str(self._parent_id())
            inputs = {"tracks": keys.job_results_prefix(org, vid, parent) + "tracks/" + TRACKS_FILE, "shots": shots}
            return CpuTask(task_id=CpuTask.make_id(job, RECOMPUTE), stage="recompute",
                           output_prefix=self._prefix(key), inputs=inputs,
                           params={**ids, "parent_job_id": parent, "calibration": self.detail.get("calibration")},
                           timeout_s=TIMEOUT_S[RECOMPUTE], **common)  # fmt: skip
        video = store.get_video(self.conn, self.video_id) or {}
        revision = int(video.get("revision") or 0)
        if key == TILES:
            media = self._media()
            params = {**ids, "revision": revision, "duration_ms": int(media.get("duration_ms") or 0)
                      or self._timeline().shots[-1].t1_ms, "frame_count": media.get("frame_count")}  # fmt: skip
            return CpuTask(task_id=CpuTask.make_id(job, TILES), stage="tiles", output_prefix=self._prefix(key),
                           inputs={"tracks": tracks, "shots": shots}, params=params, timeout_s=TIMEOUT_S[TILES],
                           **common)  # fmt: skip
        inputs = {"tracks": tracks, "team_model": self._prefix(CALIBRATE) + TEAM_MODEL_FILE}
        return CpuTask(task_id=CpuTask.make_id(job, LOAD_DB), stage="load_db", output_prefix=self._prefix(key),
                       inputs=inputs, params={**ids, "revision": revision}, timeout_s=TIMEOUT_S[LOAD_DB],
                       **common)  # fmt: skip

    def _segment(self, index: int) -> Segment:
        for raw in self._segments():
            if raw["index"] == index:
                return Segment.model_validate(raw)
        raise RuntimeError(f"job {self.id}: segment {index} is not in the plan")

    def _prefix(self, key: str) -> str:
        """Object prefix of a task's outputs — of the job that really produced them when the task is inherited."""
        job = self.inherit.get(key, str(self.id))
        results = keys.job_results_prefix(str(self.org), str(self.video_id), job)
        if key == CALIBRATE:
            return results + "models/"  # contract: results/…/jobs/{job}/models/team_model.json
        if key.startswith("infer:"):
            return keys.segment_prefix(str(self.org), str(self.video_id), job, int(key.split(":")[1]))
        if key.startswith("pitch:"):  # own _SUCCESS, next to the segment's detections
            return keys.segment_prefix(str(self.org), str(self.video_id), job, int(key.split(":")[1])) + "pitch/"
        if key in (STITCH, RECOMPUTE):
            return results + "tracks/"  # contract: results/…/jobs/{job}/tracks/tracks.parquet
        return results + f"stages/{key}/"

    def _task_done(self, row: store.Row, result: dict[str, Any]) -> None:
        store.update_task(self.conn, row, status="done", payload=result, error=None, not_before=None,
                          finished_at=self.now)  # fmt: skip
        if row["task_key"] == NORMALIZE and isinstance(result.get("source"), dict):
            store.update_video_probe(self.conn, self.video_id, result["source"])

    def _task_failed(self, row: store.Row, failure: StageFailure) -> None:
        """One attempt failed: back off and retry (3×) when retryable, else the task is failed for this run."""
        error = json.dumps(failure.as_dict())
        attempts = int(row["attempts"])
        if failure.retryable and 1 <= attempts <= MAX_RETRIES and row["task_key"] != PLAN:
            delay = BACKOFF_S[attempts - 1]
            store.update_task(self.conn, row, status="queued", external_id=None, error=error,
                              not_before=self.now + timedelta(seconds=delay))  # fmt: skip
            self._wake(delay)
            log.warning("job %s: %s attempt %d failed (%s); retry in %ds", self.id, row["task_key"], attempts,
                        failure, delay)  # fmt: skip
        else:
            store.update_task(self.conn, row, status="failed", error=error, finished_at=self.now)
            log.warning("job %s: %s failed after %d attempt(s): %s", self.id, row["task_key"], attempts, failure)

    def _task_failure(self, key: str) -> StageFailure:
        raw = self.tasks[key].get("error")
        try:
            return StageFailure.from_dict(json.loads(raw) if raw else None)
        except ValueError:
            return StageFailure(ErrorCode.INTERNAL, str(raw), retryable=True)

    def _wake(self, delay_s: float) -> None:
        delay_s = max(1.0, delay_s)
        self.wake_in = delay_s if self.wake_in is None else min(self.wake_in, delay_s)

    # ------------------------------------------------------------------ completion tracking
    def record(self, rep: Report) -> None:
        job_id, key = task_key_of(rep.task_id)
        row = self.tasks.get(key)
        if job_id != self.id or row is None or row["status"] in ("done", "failed"):
            return
        if rep.ok:  # a success is valid whichever attempt produced it: _SUCCESS was written
            self._task_done(row, rep.result or {})
        elif row["status"] == "running" and row["external_id"] in (None, rep.external_id):
            self._task_failed(row, StageFailure.from_dict(rep.error))
        else:
            log.info("job %s: ignoring stale failure of %s (%s)", self.id, key, rep.external_id)

    def _poll_inflight(self) -> None:
        for key, row in list(self.tasks.items()):
            if row["status"] != "running" or key == PLAN:
                continue
            since = row["dispatched_at"] or row["updated_at"] or self.now
            age = (self.now - since).total_seconds()
            if age < POLL_AFTER_S:  # the result message normally arrives first; the sweep polls again later
                continue
            marker = success_key(self._prefix(key))
            if self.deps.store.exists(marker):
                self._task_done(row, json.loads(self.deps.store.get_bytes(marker) or b"{}"))
                continue
            try:
                state = self.deps.router.get(row["backend"]).status(
                    TaskHandle(backend=row["backend"] or "local", external_id=row["external_id"] or ""))
            except Exception as e:  # status is advisory
                log.debug("job %s: status of %s unavailable: %r", self.id, key, e)
                state = "queued"
            timeout = TIMEOUT_S.get(key.split(":")[0], 1800)
            if state == "failed" or state == "done":  # done without _SUCCESS = the stage didn't finish its contract
                self._task_failed(row, StageFailure(ErrorCode.INTERNAL, f"{key} {state} on backend without "
                                                    "_SUCCESS", retryable=True))  # fmt: skip
            elif state == "running" and age > timeout + RUNNING_GRACE_S:
                self._task_failed(row, StageFailure(ErrorCode.INTERNAL, f"{key} timed out", retryable=True))
            elif state == "queued" and age > STALE_QUEUED_S:
                self._task_failed(row, StageFailure(ErrorCode.INTERNAL, f"{key} lost in queue", retryable=True))

    def _revoke_inflight(self) -> None:
        for key, row in self.tasks.items():
            if row["status"] == "running" and row["external_id"]:
                try:
                    self.deps.router.get(row["backend"]).cancel(
                        TaskHandle(backend=row["backend"] or "local", external_id=row["external_id"]))
                except Exception as e:
                    log.warning("job %s: revoke %s failed: %r", self.id, key, e)
                err = json.dumps({"code": "INTERNAL", "retryable": False, "detail": "cancelled"})
                store.update_task(self.conn, row, status="failed", error=err, finished_at=self.now)
                log.info("job %s cancelled: revoked %s", self.id, key)

    # ------------------------------------------------------------------ progress
    def _progress(self) -> None:
        done = {k for k, r in self.tasks.items() if r["status"] == "done"}
        pct = sum(WEIGHTS[k] for k in (NORMALIZE, SHOTS, CALIBRATE) if k in done)
        segs = self._segments() if self.kind != "recalibrate" else []
        counts: tuple[int, int, int] | None = None
        eta_s: int | None = None
        if self.kind == "recalibrate":
            pct += WEIGHTS["infer"]  # reused from the parent
        if segs:
            seg_rows = [self.tasks.get(infer_key(s["index"])) for s in segs]
            n_done = sum(1 for r in seg_rows if r and r["status"] == "done")
            running = sum(1 for r in seg_rows if r and r["status"] == "running")
            counts = (n_done, running, len(segs))
            pct += WEIGHTS["infer"] * n_done / len(segs)
            eta_s = self._eta(seg_rows, len(segs) - n_done, running)
        post = self._post_stages()
        pct += WEIGHTS["post"] * sum(1 for k in post if k in done) / len(post)
        if self.status == JobStatus.done:
            pct, eta_s = 100.0, 0
        pct = round(min(pct, 100.0), 2)
        detail: dict[str, Any] = {k: self.detail[k] for k in DETAIL_KEEP if k in self.detail}
        detail |= {"segments": dict(zip(("done", "running", "total"), counts, strict=True)) if counts else None,
                   "fps": None, "eta_s": eta_s}  # fmt: skip
        pitch = self._pitch_status()
        if pitch:
            detail["pitch_calibration"] = pitch  # LatestJob.pitch_calibration (API)
        changed = (float(self.job["progress"]) != pct or self.job["stage"] != self.stage
                   or (self.job["detail"] or {}).get("segments") != detail["segments"])  # fmt: skip
        store.update_job(self.conn, self.id, progress=pct, stage=self.stage, detail=detail)
        if changed and self.status not in (JobStatus.failed, JobStatus.cancelled, JobStatus.done):
            self.events.append(events.progress(self.id, self.stage or self.status.value, pct, segments=counts,
                                               eta_s=eta_s))  # fmt: skip

    def _eta(self, rows: list[store.Row | None], remaining: int, running: int) -> int | None:
        took = [(r["finished_at"] - r["dispatched_at"]).total_seconds() for r in rows
                if r and r["status"] == "done" and r["finished_at"] and r["dispatched_at"]]  # fmt: skip
        if not took or remaining <= 0:
            return None if remaining > 0 else 0
        return int(sum(took) / len(took) * remaining / max(1, running))

