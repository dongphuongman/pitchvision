"""Celery entry points of the local backends: ``gpu.run_stage`` (queue PV_LOCAL_GPU_QUEUE) and ``cpu.run_stage``.

Both execute the task, then report to the orchestrator (``orchestrate.on_task_result``) and return
``{"ok", "result", "error"}`` — a handled stage failure is a *successful* Celery task carrying ok=false, so the
backend status and the message agree. acks_late (app default): a worker that dies mid-task gets it redelivered,
and the redelivery skips straight to the result if ``_SUCCESS`` was already written.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from typing import Any

from celery import Task
from celery.exceptions import SoftTimeLimitExceeded
from pitchvision_schemas import CpuTask, ErrorCode, GpuTask
from sqlalchemy import select

from pitchvision_worker import repository
from pitchvision_worker.celery_app import QUEUE_ORCHESTRATE, TASK_ON_RESULT, app
from pitchvision_worker.compute.base import StageFailure, StageTask, classify
from pitchvision_worker.compute.executor import execute
from pitchvision_worker.compute.local import TASK_RUN_CPU, TASK_RUN_GPU
from pitchvision_worker.config import WorkerConfig
from pitchvision_worker.storage import ObjectStore

log = logging.getLogger(__name__)

Reporter = Callable[[str, str, dict[str, Any]], None]


# Stage tasks: if the process dies (OOM, SIGKILL) record FAILURE at once instead of letting the broker
# redeliver — the orchestrator owns retries (3 attempts + backoff), and a redelivered OOM task just dies again.
# Without this the result stays STARTED and the orchestrator waits out the full stage timeout (30 min for S4).
@app.task(name=TASK_RUN_GPU, bind=True, reject_on_worker_lost=False)
def run_gpu_stage(self: Task, task: dict[str, Any]) -> dict[str, Any]:
    return run_and_report(GpuTask.model_validate(task), str(self.request.id), job_status=db_job_status)


@app.task(name=TASK_RUN_CPU, bind=True, reject_on_worker_lost=False)
def run_cpu_stage(self: Task, task: dict[str, Any]) -> dict[str, Any]:
    return run_and_report(CpuTask.model_validate(task), str(self.request.id), job_status=db_job_status)


ACTIVE = frozenset({"queued", "ingesting", "calibrating", "inferring", "postprocessing"})


def db_job_status(job_id: str, cfg: WorkerConfig) -> str | None:
    """Status of the task's job (None = unknown: DB unreachable / no such job → run anyway)."""
    try:
        engine = repository.engine_for(cfg.database_url)
        org = repository.job_org(engine, uuid.UUID(job_id))
        if org is None:
            return None
        with repository.org_tx(engine, org) as conn:
            status: str | None = conn.execute(select(repository.jobs.c.status)
                                              .where(repository.jobs.c.id == uuid.UUID(job_id))).scalar()  # fmt: skip
            return status
    except Exception as e:  # advisory: never block a stage on this check
        log.debug("job status of %s unavailable: %r", job_id, e)
        return None


def run_and_report(task: StageTask, external_id: str, store: ObjectStore | None = None,
                   cfg: WorkerConfig | None = None, notify: Reporter | None = None,
                   job_status: Callable[[str, WorkerConfig], str | None] | None = None) -> dict[str, Any]:  # fmt: skip
    """Run one stage and report it. ``job_status``: skip the stage when its job is no longer active — a cancelled
    job's queued tasks are revoked only in the memory of the workers running at that moment, so after a worker
    restart they would otherwise still run (seen on the compose stack: hours of CPU S4 for a cancelled job)."""
    cfg = cfg or WorkerConfig.from_env()
    status = job_status(task.task_id.split(":")[0], cfg) if job_status else None
    if status is not None and status not in ACTIVE:
        log.info("%s skipped: job is %s", task.task_id, status)
        failure = StageFailure(ErrorCode.INTERNAL, f"job is {status}; stage skipped", retryable=False)
        outcome_skip: dict[str, Any] = {"ok": False, "result": None, "error": failure.as_dict(), "skipped": True}
        (notify or report)(task.task_id, external_id, outcome_skip)
        return outcome_skip
    try:
        result = execute(task, store or ObjectStore.from_config(cfg), image_tag=cfg.image_tag)
        outcome: dict[str, Any] = {"ok": True, "result": result, "error": None}
    except SoftTimeLimitExceeded:
        failure = StageFailure(ErrorCode.INTERNAL, f"timed out after {task.timeout_s}s", retryable=True)
        outcome = {"ok": False, "result": None, "error": failure.as_dict()}
    except Exception as e:
        failure = classify(e)
        log.warning("%s failed: %s", task.task_id, failure, exc_info=failure.code == ErrorCode.INTERNAL)
        outcome = {"ok": False, "result": None, "error": failure.as_dict()}
    (notify or report)(task.task_id, external_id, outcome)
    return outcome


def report(task_id: str, external_id: str, outcome: dict[str, Any]) -> None:
    """Fast path back to the orchestrator; if it is lost the orchestrator still sees `_SUCCESS` / the task state."""
    try:
        app.send_task(TASK_ON_RESULT, kwargs={"task_id": task_id, "external_id": external_id, **outcome},
                      queue=QUEUE_ORCHESTRATE)  # fmt: skip
    except Exception as e:
        log.warning("%s: could not report result to the orchestrator: %r", task_id, e)
