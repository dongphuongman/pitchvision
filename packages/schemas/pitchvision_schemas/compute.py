"""ComputeBackend payloads (AD §7, §10.4 "GpuTask / CpuTask"). Inputs/outputs are object keys; no shared filesystem."""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from pitchvision_schemas.pipeline import PipelineConfig, Segment

GpuStage = Literal["calibrate", "infer", "ocr", "pitch", "render", "shots"]
CpuStage = Literal["normalize", "stitch", "tiles", "load_db"]
TaskStatus = Literal["queued", "running", "done", "failed"]


def make_task_id(job_id: str, stage: str, segment: int | None = None) -> str:
    return f"{job_id}:{stage}:{segment:03d}" if segment is not None else f"{job_id}:{stage}"


class GpuTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(
        ..., description='idempotency key = f"{job_id}:{stage}:{segment:03d}" (segment: infer / pitch only)')
    stage: GpuStage
    image_tag: str = Field(..., description="must match across backends (deterministic results)")
    inputs: dict[str, str] = Field(default_factory=dict, description="name → object key")
    output_prefix: str
    config: PipelineConfig
    segment: Segment | None = Field(
        None, description="the S4 unit of work (stages infer / pitch); None for whole-video stages")
    timeout_s: int = 1800

    @staticmethod
    def make_id(job_id: str, stage: str, segment: int | None = None) -> str:
        return make_task_id(job_id, stage, segment)


class CpuTask(BaseModel):
    """CPU-queue twin of GpuTask for stages that never need a GPU (S2 normalise, S5 stitch, S7 tiles, S8 load-db);
    always runs on the local cpu queue."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    stage: CpuStage
    image_tag: str
    inputs: dict[str, str] = Field(default_factory=dict, description="name → object key")
    outputs: dict[str, str] = Field(default_factory=dict, description="name → exact key for outputs outside the prefix")
    output_prefix: str
    config: PipelineConfig
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="stage arguments that are not object keys: org/video/job ids, revision, duration, segment plan")
    timeout_s: int = 3600

    @staticmethod
    def make_id(job_id: str, stage: str) -> str:
        return make_task_id(job_id, stage)


class TaskHandle(BaseModel):
    backend: str
    external_id: str


class Capacity(BaseModel):
    free_slots: int
    queue_depth: int
    queue_wait_est_s: float
    cost_per_hour_usd: float = 0.0
