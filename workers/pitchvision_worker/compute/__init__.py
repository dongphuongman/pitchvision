"""GPU/CPU stage dispatch (AD §7): ComputeBackend protocol, local Celery backends, RunPod stub, router, executor."""

from pitchvision_worker.compute.base import (
    ComputeBackend,
    CpuTask,
    StageFailure,
    StageTask,
    classify,
    success_key,
)

__all__ = ["ComputeBackend", "CpuTask", "StageFailure", "StageTask", "classify", "success_key"]
