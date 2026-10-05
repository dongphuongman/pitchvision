"""ComputeBackend payloads (AD §7). Inputs/outputs are object keys; no shared filesystem."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from pitchvision_schemas.pipeline import PipelineConfig

GpuStage = Literal["calibrate", "infer", "ocr", "render", "shots"]
TaskStatus = Literal["queued", "running", "done", "failed"]


class GpuTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(..., description='idempotency key = f"{job_id}:{stage}:{segment}"')
    stage: GpuStage
    image_tag: str = Field(..., description="must match across backends (deterministic results)")
    inputs: dict[str, str] = Field(default_factory=dict, description="name → object key")
    output_prefix: str
    config: PipelineConfig
    timeout_s: int = 1800

    @staticmethod
    def make_id(job_id: str, stage: str, segment: int | None = None) -> str:
        return f"{job_id}:{stage}:{segment:03d}" if segment is not None else f"{job_id}:{stage}"


class TaskHandle(BaseModel):
    backend: str
    external_id: str


class Capacity(BaseModel):
    free_slots: int
    queue_depth: int
    queue_wait_est_s: float
    cost_per_hour_usd: float = 0.0
