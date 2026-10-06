"""RunPodBackend (AD §7, W5): POST /v2/{endpoint}/run with the GpuTask JSON; the handler in the same image runs
``pitchvision_worker.compute.executor.execute`` against R2 via short-lived presigned URLs, writes ``_SUCCESS``, and
``status`` polls /status. Interface only for now — every call raises NotImplementedError.
"""

from __future__ import annotations

from pitchvision_schemas import Capacity, TaskHandle, TaskStatus

from pitchvision_worker.compute.base import StageTask


class RunPodBackend:
    name = "runpod"

    def __init__(self, endpoint_id: str | None = None, api_key: str | None = None) -> None:
        self.endpoint_id, self.api_key = endpoint_id, api_key

    def submit(self, task: StageTask) -> TaskHandle:
        raise NotImplementedError("RunPodBackend lands in W5 (M1-BE RunPod)")

    def status(self, h: TaskHandle) -> TaskStatus:
        raise NotImplementedError("RunPodBackend lands in W5 (M1-BE RunPod)")

    def cancel(self, h: TaskHandle) -> None:
        raise NotImplementedError("RunPodBackend lands in W5 (M1-BE RunPod)")

    def capacity(self) -> Capacity:
        raise NotImplementedError("RunPodBackend lands in W5 (M1-BE RunPod)")
