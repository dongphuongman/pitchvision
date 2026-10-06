"""Job DAG driver (AD §6.1, §8.2, §16; docs/contracts/pipeline-stages.md).

``advance(job_id, org_id)`` is ONE idempotent step function; everything that can change a job calls it:
``start_job``, every stage result (``on_task_result``), retry-backoff timers, the ingest worker when a YouTube video
becomes ready, and the periodic/startup ``sweep``. Each call opens one RLS-scoped transaction, locks the job row
(``FOR UPDATE`` — concurrent calls serialise, so nothing is dispatched twice), reloads ``job_tasks`` and moves the
job as far as it can without waiting:

    queued → ingesting (wait for the video to be ready) → calibrating: normalize (cpu) → shots (gpu) → plan (inline)
    → calibrate (gpu) → inferring: infer:NNN fan-out (gpu, ≤ max_inflight) → postprocessing: stitch/tiles/load_db
    (W3 stubs) → done

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

from pitchvision_schemas import ErrorCode, GpuTask, JobStatus, PipelineConfig, ShotTimeline, TaskHandle, keys
from pitchvision_schemas.jobs import can_transition
from sqlalchemy import Connection, Engine

from pitchvision_worker import events
from pitchvision_worker.compute.base import CpuTask, StageFailure, StageTask, classify, success_key
from pitchvision_worker.compute.router import BackendRouter
from pitchvision_worker.compute.stages import PLANNERS, SEGMENTS_FILE, SHOTS_FILE, SegmentPlanner
from pitchvision_worker.orchestrator import store
from pitchvision_worker.repository import org_tx
from pitchvision_worker.storage import ObjectStore

log = logging.getLogger(__name__)

NORMALIZE, SHOTS, PLAN, CALIBRATE = "normalize", "shots", "plan", "calibrate"
POST_STAGES = ("stitch", "tiles", "load_db")  # S5, S7, S8: stubs until W4 (M1-CV-13, M1-BE-13/14)
BACKOFF_S = (30, 120, 300)  # AD §16.1: retry a task 3 times, then it fails
MAX_RETRIES = len(BACKOFF_S)
POLL_AFTER_S = 60  # check backend/_SUCCESS for dispatched tasks silent for this long (the result message is faster)
STALE_QUEUED_S = 6 * 3600  # = broker visibility timeout: a message still PENDING after this is considered lost
RUNNING_GRACE_S = 600
TIMEOUT_S = {NORMALIZE: 3 * 3600, SHOTS: 2 * 3600, CALIBRATE: 1800, "infer": 1800}
WEIGHTS = {NORMALIZE: 5.0, SHOTS: 5.0, CALIBRATE: 5.0, "infer": 80.0, "post": 5.0}  # AD §8.2: S4 is 80 %
TERMINAL = {JobStatus.done, JobStatus.failed, JobStatus.cancelled}


def infer_key(i: int) -> str:
    return f"infer:{i:03d}"


def task_key_of(task_id: str) -> tuple[uuid.UUID, str]:
    """GpuTask.make_id(job, stage, seg) → (job_id, job_tasks.task_key)."""
    job, stage, *seg = task_id.split(":")
    return uuid.UUID(job), (infer_key(int(seg[0])) if stage == "infer" and seg else stage)


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


def advance(job_id: uuid.UUID, org_id: uuid.UUID, deps: Deps, report: Report | None = None) -> str:
    """One step; returns the job status afterwards ("missing" if the job isn't visible to this org)."""
    with org_tx(deps.engine, org_id) as conn:
        job = store.lock_job(conn, job_id)
        if job is None:
            return "missing"
        run = _Run(conn, job, deps)
        if report is not None:
            run.record(report)
        run.drive()
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

    # ------------------------------------------------------------------ driving
    def drive(self) -> None:
        if self.status == JobStatus.cancelled:
            self._revoke_inflight()
            return
        if self.status in TERMINAL:
            return
        try:
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
                self._postprocess()
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
        self.deps.store.copy(self._prefix(SHOTS) + SHOTS_FILE, _media_shots_key(str(self.org), str(self.video_id)))
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
        plan = {"segments": [s.model_dump() for s in segments], "main_wide_pct": pct, "shots": n}
        self.deps.store.put_bytes(self.results + SEGMENTS_FILE, json.dumps({"segments": plan["segments"]}).encode())
        self._task_done(row, plan)
        log.info("job %s: %d shots, main_wide %.1f%%, %d segment(s)", self.id, n, pct, len(segments))
        return True

    def _segments(self) -> list[dict[str, Any]]:
        segs: list[dict[str, Any]] = (self.tasks.get(PLAN, {}).get("payload") or {}).get("segments") or []
        return segs

    def _inferring(self) -> bool:
        self.stage = "infer"
        states = [self._ensure(infer_key(s["index"])) for s in self._segments()]
        if any(s == "waiting" for s in states):
            return False
        failed = [infer_key(s["index"]) for s, st in zip(self._segments(), states, strict=True) if st == "failed"]
        if failed:
            first = self._task_failure(failed[0])
            detail = f"{len(failed)}/{len(states)} segment(s) failed; first ({failed[0]}): {first.detail}"
            self._fail_job(StageFailure(first.code, detail, first.retryable))
        return True

    def _postprocess(self) -> None:
        for key in POST_STAGES:  # TODO(W4): S5 stitch (M1-CV-13), S7 tiles (M1-BE-13), S8 load-db (M1-BE-14)
            self.stage = key
            row = self.tasks.get(key) or self._insert(key)
            if row["status"] != "done":
                log.info("job %s: %s is a stub in W3, marking done", self.id, key)
                self._task_done(row, {"stub": True})

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
        if key.startswith("infer:") and self._inflight("infer:") >= self.deps.max_inflight:
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
            return CpuTask(task_id=GpuTask.make_id(job, NORMALIZE), stage="normalize", output_prefix=self._prefix(key),
                           inputs={"source": self._source_key()}, outputs={"normalized": normalized},
                           timeout_s=TIMEOUT_S[NORMALIZE], **common)  # fmt: skip
        if key == SHOTS:
            return GpuTask(task_id=GpuTask.make_id(job, SHOTS), stage="shots", output_prefix=self._prefix(key),
                           inputs={"normalized": normalized}, timeout_s=TIMEOUT_S[SHOTS], **common)  # fmt: skip
        if key == CALIBRATE:
            inputs = {"normalized": normalized, "shots": self._prefix(SHOTS) + SHOTS_FILE}
            return GpuTask(task_id=GpuTask.make_id(job, CALIBRATE), stage="calibrate", output_prefix=self._prefix(key),
                           inputs=inputs, timeout_s=TIMEOUT_S[CALIBRATE], **common)  # fmt: skip
        index = int(key.split(":")[1])
        inputs = {"normalized": normalized, "team_model": self._prefix(CALIBRATE) + "team_model.json",
                  "segments": self.results + SEGMENTS_FILE}  # fmt: skip
        return GpuTask(task_id=GpuTask.make_id(job, "infer", index), stage="infer", output_prefix=self._prefix(key),
                       inputs=inputs, timeout_s=TIMEOUT_S["infer"], **common)  # fmt: skip

    def _prefix(self, key: str) -> str:
        if key == CALIBRATE:
            return self.results + "models/"  # contract: results/…/jobs/{job}/models/team_model.json
        if key.startswith("infer:"):
            return keys.segment_prefix(str(self.org), str(self.video_id), str(self.id), int(key.split(":")[1]))
        return self.results + f"stages/{key}/"

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
        segs = self._segments()
        counts: tuple[int, int, int] | None = None
        eta_s: int | None = None
        if segs:
            seg_rows = [self.tasks.get(infer_key(s["index"])) for s in segs]
            n_done = sum(1 for r in seg_rows if r and r["status"] == "done")
            running = sum(1 for r in seg_rows if r and r["status"] == "running")
            counts = (n_done, running, len(segs))
            pct += WEIGHTS["infer"] * n_done / len(segs)
            eta_s = self._eta(seg_rows, len(segs) - n_done, running)
        pct += WEIGHTS["post"] * sum(1 for k in POST_STAGES if k in done) / len(POST_STAGES)
        if self.status == JobStatus.done:
            pct, eta_s = 100.0, 0
        pct = round(min(pct, 100.0), 2)
        detail = {"segments": dict(zip(("done", "running", "total"), counts, strict=True)) if counts else None,
                  "fps": None, "eta_s": eta_s}  # fmt: skip
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


def _media_shots_key(org_id: str, video_id: str) -> str:
    """media/orgs/{org}/videos/{video}/shots.json — TODO(schemas): keys.shots_key (see report)."""
    return keys.manifest_key(org_id, video_id).rsplit("/", 1)[0] + "/shots.json"
