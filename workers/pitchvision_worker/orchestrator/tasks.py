"""Orchestrator Celery tasks (queue ``orchestrate``). Every one of them ends in ``engine.advance``.

- ``orchestrate.start_job(job_id, org_id?)``   API, on create and retry. Published before the API commits: a job not
                                               visible yet is retried for a few seconds.
- ``orchestrate.cancel_job(job_id, org_id?)``  API, on cancel: revokes dispatched tasks.
- ``orchestrate.advance(job_id, org_id)``      retry-backoff timers.
- ``orchestrate.on_task_result(task_id, …)``   executor → orchestrator fast path.
- ``orchestrate.video_ready(video_id, org_id)`` ingest worker: a YouTube download finished (or failed).
- ``orchestrate.sweep()``                      every PV_SWEEP_INTERVAL_S (embedded beat of the orchestrator worker)
                                               and once at orchestrator startup: resumes every job in a non-terminal
                                               state from job_tasks (AD §16.1 "orchestrator restarts").
"""

from __future__ import annotations

import logging
import os
import uuid
from functools import lru_cache
from typing import Any

from celery import Task
from celery.signals import worker_ready

from pitchvision_worker import repository
from pitchvision_worker.celery_app import (
    QUEUE_ORCHESTRATE,
    TASK_ADVANCE,
    TASK_CANCEL_JOB,
    TASK_ON_RESULT,
    TASK_START_JOB,
    TASK_SWEEP,
    TASK_VIDEO_READY,
    app,
)
from pitchvision_worker.compute.router import BackendRouter
from pitchvision_worker.config import WorkerConfig
from pitchvision_worker.orchestrator import engine, store
from pitchvision_worker.repository import org_tx
from pitchvision_worker.storage import ObjectStore

log = logging.getLogger(__name__)

ROW_WAIT_S, ROW_WAIT_TRIES = 2, 5  # the API publishes before its transaction commits


@lru_cache(maxsize=1)
def deps() -> engine.Deps:
    cfg = WorkerConfig.from_env()
    return engine.Deps(
        engine=repository.engine_for(cfg.database_url),
        store=ObjectStore.from_config(cfg),
        router=BackendRouter.from_config(cfg, app),
        redis=_redis(cfg),
        schedule=schedule_advance,
        max_inflight=cfg.max_inflight_segments,
    )


def schedule_advance(job_id: uuid.UUID, org_id: uuid.UUID, delay_s: float) -> None:
    app.send_task(TASK_ADVANCE, kwargs={"job_id": str(job_id), "org_id": str(org_id)}, queue=QUEUE_ORCHESTRATE,
                  countdown=delay_s)  # fmt: skip


def _redis(cfg: WorkerConfig) -> Any | None:
    try:
        import redis

        return redis.Redis.from_url(cfg.redis_url, socket_connect_timeout=2, socket_timeout=2)
    except Exception as e:
        log.warning("redis client unavailable (no progress events): %r", e)
        return None


def _org(job_id: uuid.UUID, org_id: str | None, d: engine.Deps) -> uuid.UUID | None:
    return uuid.UUID(org_id) if org_id else repository.job_org(d.engine, job_id)


# Transient errors (DB/broker/S3 hiccups) roll the step back; retry the step — it is idempotent.
_RETRY: dict[str, Any] = {"autoretry_for": (Exception,), "retry_backoff": 2, "retry_backoff_max": 60,
                          "max_retries": 8}  # fmt: skip


@app.task(name=TASK_START_JOB, bind=True, **_RETRY)
def start_job(self: Task, job_id: str, org_id: str | None = None, row_waits: int = 0) -> str:
    d = deps()
    jid = uuid.UUID(job_id)
    org = _org(jid, org_id, d)
    status = engine.advance(jid, org, d) if org else "missing"
    if status == "missing" and row_waits < ROW_WAIT_TRIES:
        raise self.retry(kwargs={"job_id": job_id, "org_id": org_id, "row_waits": row_waits + 1},
                         countdown=ROW_WAIT_S, max_retries=None)  # fmt: skip
    return status


@app.task(name=TASK_CANCEL_JOB, bind=True, **_RETRY)
def cancel_job(self: Task, job_id: str, org_id: str | None = None, waits: int = 0) -> str:
    d = deps()
    jid = uuid.UUID(job_id)
    org = _org(jid, org_id, d)
    status = engine.advance(jid, org, d) if org else "missing"
    if status != "cancelled" and waits < ROW_WAIT_TRIES:  # the API's cancel may not be committed yet
        raise self.retry(kwargs={"job_id": job_id, "org_id": org_id, "waits": waits + 1}, countdown=ROW_WAIT_S,
                         max_retries=None)  # fmt: skip
    return status


@app.task(name=TASK_ADVANCE, **_RETRY)
def advance(job_id: str, org_id: str) -> str:
    return engine.advance(uuid.UUID(job_id), uuid.UUID(org_id), deps())


@app.task(name=TASK_ON_RESULT, **_RETRY)
def on_task_result(task_id: str, external_id: str | None, ok: bool, result: dict[str, Any] | None = None,
                   error: dict[str, Any] | None = None) -> str:  # fmt: skip
    d = deps()
    jid, _ = engine.task_key_of(task_id)
    org = repository.job_org(d.engine, jid)
    if org is None:
        log.info("result for unknown job %s ignored", task_id)
        return "missing"
    return engine.advance(jid, org, d, engine.Report(task_id, external_id, ok, result, error))


@app.task(name=TASK_VIDEO_READY, **_RETRY)
def video_ready(video_id: str, org_id: str) -> int:
    d = deps()
    org = uuid.UUID(org_id)
    with org_tx(d.engine, org) as conn:
        job_ids = store.active_jobs_for_video(conn, uuid.UUID(video_id))
    for jid in job_ids:
        engine.advance(jid, org, d)
    return len(job_ids)


@app.task(name=TASK_SWEEP, acks_late=False)
def sweep() -> int:
    return run_sweep(deps())


def run_sweep(d: engine.Deps) -> int:
    """Resume every job that isn't finished (and revoke tasks of cancelled ones). Errors are per job."""
    n = 0
    for job_id, org_id in repository.active_jobs(d.engine):
        try:
            engine.advance(job_id, org_id, d)
            n += 1
        except Exception:
            log.exception("sweep: advancing job %s failed", job_id)
    return n


@worker_ready.connect
def _recover_on_start(**_: Any) -> None:
    """Orchestrator restart: pick up every running job from job_tasks right away (AD §16.1)."""
    if os.environ.get("WORKER_ROLE") == "orchestrator":
        log.info("orchestrator ready: sweeping non-terminal jobs")
        app.send_task(TASK_SWEEP, queue=QUEUE_ORCHESTRATE)
