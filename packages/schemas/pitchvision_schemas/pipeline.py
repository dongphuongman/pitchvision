"""PipelineConfig — everything a job needs to be reproducible (stored in jobs.config)."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PipelineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    target_fps: int = 25                       # S2 normalises 50/60 fps broadcast to 25 (AD §6)
    long_side_px: int = 1280                   # normalized.mp4 long side
    keyframe_interval_s: int = 2
    frame_stride: Literal[1, 2] = 1            # 2 = Fast mode
    detector: str = "dfine-m"                  # G-LEGAL 05/10/2026: D-FINE (Apache-2.0), see SP-11
    detector_imgsz: int = 640
    conf_threshold: float = Field(0.35, ge=0.0, le=1.0)
    keypoint_every_n: int = Field(5, ge=1)
    analyze_shot_types: tuple[str, ...] = ("main_wide",)  # S3/S4 only run on these (S2b)
    min_shot_ms: int = 1000                    # shorter shots are merged into neighbours
    segment_target_s: int = 120                # segments are cut on shot boundaries, merged up to ~120 s
    format: Literal["11v11"] = "11v11"         # 9v9/7v7 → Phase C


class Segment(BaseModel):
    """A unit of S4 work: half-open [t0_ms, t1_ms) on the normalized timeline, inside main_wide shots."""

    model_config = ConfigDict(frozen=True)

    index: int = Field(..., ge=0)
    t0_ms: int = Field(..., ge=0)
    t1_ms: int

    @model_validator(mode="after")
    def _ordered(self) -> "Segment":
        if self.t1_ms <= self.t0_ms:
            raise ValueError("t1_ms must be > t0_ms")
        return self
