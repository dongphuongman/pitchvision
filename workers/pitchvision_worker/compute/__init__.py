"""GPU/CPU stage dispatch (AD §7): ComputeBackend protocol, local Celery backends, RunPod stub, router, executor."""

from pitchvision_schemas import CpuTask, GpuTask

from pitchvision_worker.compute.base import (
    ComputeBackend,
    StageFailure,
    StageTask,
    classify,
    success_key,
)

__all__ = ["ComputeBackend", "CpuTask", "GpuTask", "StageFailure", "StageTask", "classify", "success_key"]
