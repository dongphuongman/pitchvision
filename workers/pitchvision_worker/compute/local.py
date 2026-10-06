"""LocalGpuBackend (AD §7): Celery task ``gpu.run_stage`` on queue ``PV_LOCAL_GPU_QUEUE`` (default ``gpu``).

On a CPU-only box set ``PV_LOCAL_GPU_QUEUE=cpu`` and the cpu-worker runs the GPU stages (slowly) — same code path.
``LocalCpuBackend`` is the same mechanism for CpuTask stages (``cpu.run_stage`` on ``cpu``).
"""

from __future__ import annotations

import logging
from typing import Any

from celery import Celery
from celery.result import AsyncResult
from pitchvision_schemas import Capacity, TaskHandle, TaskStatus

from pitchvision_worker.compute.base import StageTask

log = logging.getLogger(__name__)

TASK_RUN_GPU = "gpu.run_stage"
TASK_RUN_CPU = "cpu.run_stage"
TIME_LIMIT_GRACE_S = 120  # hard kill this long after the soft limit (the task reports a timeout failure first)

_STATES: dict[str, TaskStatus] = {
    "PENDING": "queued",  # also "unknown id": the orchestrator's staleness rule covers lost messages
    "RECEIVED": "queued",
    "STARTED": "running",
    "RETRY": "running",
    "FAILURE": "failed",
    "REVOKED": "failed",
}


class LocalBackend:
    def __init__(self, app: Celery, queue: str, task_name: str, *, name: str = "local", slots: int = 16) -> None:
        self.app, self.queue, self.task_name, self.name, self.slots = app, queue, task_name, name, slots

    def submit(self, task: StageTask) -> TaskHandle:
        result = self.app.send_task(
            self.task_name,
            kwargs={"task": task.model_dump(mode="json")},
            queue=self.queue,
            soft_time_limit=task.timeout_s,
            time_limit=task.timeout_s + TIME_LIMIT_GRACE_S,
        )
        return TaskHandle(backend=self.name, external_id=str(result.id))

    def status(self, h: TaskHandle) -> TaskStatus:
        res = AsyncResult(h.external_id, app=self.app)
        state = str(res.state)
        if state == "SUCCESS":  # the task returns {"ok": bool, ...}; a handled stage failure is still SUCCESS
            value: Any = res.result
            return "done" if isinstance(value, dict) and value.get("ok") else "failed"
        return _STATES.get(state, "queued")

    def cancel(self, h: TaskHandle) -> None:
        """Revoke: a queued task is dropped when a worker gets it; a running one finishes (result ignored)."""
        self.app.control.revoke(h.external_id)

    def capacity(self) -> Capacity:
        depth = 0
        try:
            with self.app.connection_for_write() as conn:
                depth = int(conn.default_channel.client.llen(self.queue))
        except Exception as e:  # capacity is advisory
            log.debug("capacity: queue depth unavailable: %r", e)
        return Capacity(free_slots=self.slots, queue_depth=depth, queue_wait_est_s=0.0)


class LocalGpuBackend(LocalBackend):
    def __init__(self, app: Celery, queue: str = "gpu", *, slots: int = 16) -> None:
        super().__init__(app, queue, TASK_RUN_GPU, name="local", slots=slots)


class LocalCpuBackend(LocalBackend):
    def __init__(self, app: Celery, queue: str = "cpu", *, slots: int = 16) -> None:
        super().__init__(app, queue, TASK_RUN_CPU, name="local-cpu", slots=slots)
