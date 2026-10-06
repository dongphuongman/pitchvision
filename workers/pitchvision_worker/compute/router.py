"""Backend routing (AD §7.1): per task, not per job — one job may have segments on several backends.

Config: ``PV_COMPUTE_BACKENDS`` = priority order (default ``local``). Only ``local`` is implemented; ``runpod`` is the
W5 stub (selecting it fails the task with NotImplementedError → INTERNAL). Burst rule: skip a backend whose
estimated queue wait exceeds ``burst_after_s`` when a later one exists. CpuTasks always run on the local cpu queue.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence

from celery import Celery
from pitchvision_schemas import CpuTask

from pitchvision_worker.compute.base import ComputeBackend, StageTask
from pitchvision_worker.compute.local import LocalCpuBackend, LocalGpuBackend
from pitchvision_worker.compute.runpod import RunPodBackend
from pitchvision_worker.config import WorkerConfig

log = logging.getLogger(__name__)

BURST_AFTER_S = 600.0  # AD §7.1: burst to the next backend when local queue_wait_est > 10 min


class BackendRouter:
    def __init__(self, backends: Mapping[str, ComputeBackend], order: Sequence[str], cpu: ComputeBackend,
                 *, burst_after_s: float = BURST_AFTER_S) -> None:  # fmt: skip
        unknown = [n for n in order if n not in backends]
        if unknown or not order:
            raise ValueError(f"compute backends {unknown or order!r} not available; have {sorted(backends)}")
        self.backends, self.order, self.cpu, self.burst_after_s = dict(backends), list(order), cpu, burst_after_s

    @classmethod
    def from_config(cls, cfg: WorkerConfig, app: Celery) -> BackendRouter:
        slots = cfg.max_inflight_segments
        cpu = LocalCpuBackend(app, slots=slots)
        available: dict[str, ComputeBackend] = {"local": LocalGpuBackend(app, cfg.local_gpu_queue, slots=slots),
                                                "runpod": RunPodBackend()}  # fmt: skip
        return cls(available | {cpu.name: cpu}, cfg.compute_backends, cpu)

    def for_task(self, task: StageTask) -> ComputeBackend:
        if isinstance(task, CpuTask):
            return self.cpu
        for i, name in enumerate(self.order):
            backend = self.backends[name]
            if i == len(self.order) - 1:
                return backend
            try:
                if backend.capacity().queue_wait_est_s <= self.burst_after_s:
                    return backend
            except NotImplementedError:
                continue
            log.info("router: %s queue wait above %.0fs, bursting %s", name, self.burst_after_s, task.task_id)
        raise AssertionError("unreachable")

    def get(self, name: str | None) -> ComputeBackend:
        """Backend that owns an existing handle (job_tasks.backend)."""
        return self.backends[name or self.order[0]]
