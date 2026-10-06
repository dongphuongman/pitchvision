"""Exports (SCR-25 / SCR-26, M1-BE-17): Celery ``cpu.render_export`` and the periodic ``cpu.sweep_exports``.

``cpu.render_export {export_id, org_id}`` (published by the API, possibly before its transaction commits):
1. load the exports row (RLS-scoped; a few short retries while it is not visible yet); anything but ``queued`` is a
   redelivery / cancel → no-op. ``queued → running`` is a guarded UPDATE, so two deliveries never both render.
2. download the job's ``tracks.parquet``, presign ``normalized.mp4`` and render (``export_render.render``); every
   ~2 s ``progress`` + ``heartbeat_at`` are written, guarded on ``status = 'running'`` — a cancel / delete from the
   API flips the status and the next update sees 0 rows → the render stops and nothing is uploaded.
3. upload to ``keys.export_key`` and mark ``done`` (``expires_at = now + spec.ttl_days``). If the row was cancelled in
   the meantime the object is deleted again.
Failures are written to ``exports.error`` ``{code, retryable, detail}``; the user retries from SCR-26 (no automatic
Celery retry: a render can take tens of minutes).

``cpu.sweep_exports`` (beat, every ``PV_EXPORT_SWEEP_S``, default 10 min): fails ``running`` rows whose heartbeat is
older than ``STALE_AFTER_S`` (worker killed / OOM — retryable) and deletes the files of expired / deleted exports,
clearing ``object_key`` (``pv_stale_exports`` / ``pv_export_purge_candidates``, migration 0005).
"""

from __future__ import annotations

import logging
import tempfile
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from celery import Task
from pitchvision_schemas import ExportSpec, keys
from sqlalchemy import select, text, update

from pitchvision_worker import notify, repository
from pitchvision_worker.celery_app import QUEUE_CPU, TASK_SWEEP_EXPORTS, app
from pitchvision_worker.config import WorkerConfig
from pitchvision_worker.export_render import RenderCancelled, RenderError, render
from pitchvision_worker.repository import exports, org_tx
from pitchvision_worker.storage import ObjectStore

log = logging.getLogger(__name__)

TASK_RENDER_EXPORT = "cpu.render_export"
ROW_WAIT_S, ROW_WAIT_TRIES = 2, 5
PRESIGN_TTL_S = 6 * 3600  # ffmpeg reads normalized.mp4 for the whole render
STALE_AFTER_S = 10 * 60
PURGE_BATCH = 200
TRACKS_NAME = "tracks/tracks.parquet"


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------- rows (RLS-scoped, guarded)
def load(engine: Any, org: uuid.UUID, export_id: uuid.UUID) -> Mapping[str, Any] | None:
    with org_tx(engine, org) as conn:
        row = conn.execute(select(exports).where(exports.c.id == export_id, exports.c.org_id == org)).mappings().first()
        return dict(row) if row else None


def _guarded(engine: Any, org: uuid.UUID, export_id: uuid.UUID, status: str, values: dict[str, Any]) -> bool:
    with org_tx(engine, org) as conn:
        stmt = (update(exports)
                .where(exports.c.id == export_id, exports.c.org_id == org, exports.c.status == status,
                       exports.c.deleted_at.is_(None))
                .values(**values))  # fmt: skip
        return conn.execute(stmt).rowcount == 1


def mark_running(engine: Any, org: uuid.UUID, export_id: uuid.UUID) -> bool:
    now = _now()
    return _guarded(engine, org, export_id, "queued", {"status": "running", "started_at": now, "heartbeat_at": now,
                                                       "attempts": exports.c.attempts + 1, "progress": 0})


def set_progress(engine: Any, org: uuid.UUID, export_id: uuid.UUID, pct: float) -> bool:
    """False when the export is no longer running (cancelled / deleted) → stop."""
    return _guarded(engine, org, export_id, "running",
                    {"progress": Decimal(f"{max(0.0, min(99.0, pct)):.2f}"), "heartbeat_at": _now()})  # fmt: skip


def _finish(engine: Any, org: uuid.UUID, export_id: uuid.UUID, values: dict[str, Any], type_: str,
            extra: dict[str, Any]) -> bool:  # fmt: skip
    """running → done / failed + the requester's SCR-51 notification, in one transaction."""
    with org_tx(engine, org) as conn:
        stmt = (update(exports)
                .where(exports.c.id == export_id, exports.c.org_id == org, exports.c.status == "running",
                       exports.c.deleted_at.is_(None))
                .values(**values)
                .returning(exports.c.created_by, exports.c.video_id, exports.c.filename,
                           exports.c.attempts))  # fmt: skip
        row = conn.execute(stmt).first()
        if row is None:
            return False
        notify.add(conn, org_id=org, user_id=row.created_by, type_=type_, video_id=row.video_id,
                   data={"export_id": str(export_id), "filename": row.filename, **extra},
                   dedupe_key=f"export:{export_id}:{type_}:{row.attempts}")  # fmt: skip
        return True


def mark_done(engine: Any, org: uuid.UUID, export_id: uuid.UUID, object_key: str, size: int, ttl_days: int) -> bool:
    now = _now()
    return _finish(engine, org, export_id, {
        "status": "done", "progress": 100, "object_key": object_key, "size_bytes": size, "error": None,
        "finished_at": now, "heartbeat_at": now, "expires_at": now + timedelta(days=ttl_days)},
        "export_ready", {"size_bytes": size})


def mark_failed(engine: Any, org: uuid.UUID, export_id: uuid.UUID, code: str, detail: str, retryable: bool) -> bool:
    return _finish(engine, org, export_id, {
        "status": "failed", "finished_at": _now(),
        "error": {"code": code, "retryable": retryable, "detail": detail[:500] or None}},
        "export_failed", {"code": code})


# ---------------------------------------------------------------- render task
@dataclass
class ExportDeps:
    cfg: WorkerConfig
    engine: Any
    store: ObjectStore
    render: Callable[..., Any] = render
    tmp_root: str | None = None

    @classmethod
    def from_env(cls) -> ExportDeps:
        cfg = WorkerConfig.from_env()
        return cls(cfg, repository.engine_for(cfg.database_url), ObjectStore.from_config(cfg))


def run_render_export(org_id: str, export_id: str, deps: ExportDeps, *, row_waits: int = 0) -> str:
    """→ done | failed | cancelled | skipped | wait_row"""
    org, eid = uuid.UUID(org_id), uuid.UUID(export_id)
    row = load(deps.engine, org, eid)
    if row is None:
        return "wait_row" if row_waits < ROW_WAIT_TRIES else "skipped"
    if row["status"] != "queued" or row["deleted_at"] is not None or not mark_running(deps.engine, org, eid):
        return "skipped"
    vid = str(row["video_id"])
    try:
        spec = ExportSpec.model_validate(row["spec"])
        tracks_key = keys.job_results_prefix(org_id, vid, spec.job_id) + TRACKS_NAME
        video_key = keys.normalized_key(org_id, vid)
        if not deps.store.exists(tracks_key) or not deps.store.exists(video_key):
            raise RenderError("EXPORT_FAILED", "the analysis files are gone (video re-analysed or deleted)",
                              retryable=False)  # fmt: skip
        url = deps.store.client.generate_presigned_url(
            "get_object", Params={"Bucket": deps.store.bucket, "Key": video_key}, ExpiresIn=PRESIGN_TTL_S)
        with tempfile.TemporaryDirectory(prefix="pv-export-", dir=deps.tmp_root) as tmp:
            tracks = deps.store.download(tracks_key, Path(tmp) / "tracks.parquet")
            out = Path(tmp) / "export.mp4"
            res = deps.render(spec, url, tracks, out, on_progress=lambda pct: set_progress(deps.engine, org, eid, pct))
            object_key = keys.export_key(org_id, vid, export_id, "mp4")
            size = deps.store.upload(object_key, out)
        if not mark_done(deps.engine, org, eid, object_key, size, spec.ttl_days):
            deps.store.client.delete_object(Bucket=deps.store.bucket, Key=object_key)  # cancelled at the last second
            return "cancelled"
        log.info("export %s: %d frames %dx%d in %.0f s, %d bytes", eid, res.frames, res.width, res.height,
                 res.seconds, size)  # fmt: skip
        return "done"
    except RenderCancelled:
        log.info("export %s cancelled", eid)
        return "cancelled"
    except RenderError as e:
        log.warning("export %s failed: %s %s", eid, e.code, e.detail)
        mark_failed(deps.engine, org, eid, e.code, e.detail, e.retryable)
        return "failed"
    except Exception as e:
        log.exception("export %s crashed", eid)
        mark_failed(deps.engine, org, eid, "INTERNAL", repr(e), True)
        return "failed"


@app.task(name=TASK_RENDER_EXPORT, queue=QUEUE_CPU, bind=True, acks_late=False, max_retries=ROW_WAIT_TRIES + 1)
def render_export(self: Task, export_id: str, org_id: str, row_waits: int = 0) -> str:
    outcome = run_render_export(org_id, export_id, ExportDeps.from_env(), row_waits=row_waits)
    if outcome == "wait_row":
        raise self.retry(kwargs={"export_id": export_id, "org_id": org_id, "row_waits": row_waits + 1},
                         countdown=ROW_WAIT_S)  # fmt: skip
    return outcome


# ---------------------------------------------------------------- sweep
@dataclass(frozen=True)
class SweepResult:
    stale: int
    purged: int


def run_sweep(engine: Any, store: ObjectStore, *, stale_after_s: int = STALE_AFTER_S, batch: int = PURGE_BATCH
              ) -> SweepResult:  # fmt: skip
    with engine.begin() as conn:
        stale = [(r[0], r[1]) for r in conn.execute(text("SELECT export_id, org_id FROM pv_stale_exports(:a)"),
                                                     {"a": stale_after_s})]  # fmt: skip
        doomed = [(r[0], r[1], r[2]) for r in conn.execute(
            text("SELECT export_id, org_id, object_key FROM pv_export_purge_candidates(:n)"), {"n": batch})]
    failed = sum(mark_failed(engine, org, eid, "EXPORT_FAILED", "the export worker stopped (restart / out of "
                             "memory)", True) for eid, org in stale)  # fmt: skip
    purged = 0
    for eid, org, key in doomed:
        store.client.delete_object(Bucket=store.bucket, Key=key)
        with org_tx(engine, org) as conn:
            purged += conn.execute(update(exports).where(exports.c.id == eid, exports.c.object_key == key)
                                   .values(object_key=None)).rowcount  # fmt: skip
    if failed or purged:
        log.info("export sweep: %d stale failed, %d file(s) purged", failed, purged)
    return SweepResult(failed, purged)


@app.task(name=TASK_SWEEP_EXPORTS, queue=QUEUE_CPU, acks_late=False)
def sweep_exports() -> dict[str, int]:
    cfg = WorkerConfig.from_env()
    r = run_sweep(repository.engine_for(cfg.database_url), ObjectStore.from_config(cfg))
    return {"stale": r.stale, "purged": r.purged}
