"""Result manifest (GET /results/{id}/manifest): what the player needs before fetching tiles."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from pitchvision_schemas.shots import Shot
from pitchvision_schemas.tiles import TILE_MS


class TeamInfo(BaseModel):
    cluster: int
    name: str
    color: str = Field(..., pattern=r"^#[0-9a-fA-F]{6}$")
    confirmed: bool = False


class Coverage(BaseModel):
    main_wide_pct: float = Field(..., ge=0, le=100, description="% of duration with analysable wide shots")
    homography_ok_pct: float = Field(..., ge=0, le=100, description="% of main_wide frames with h_quality ≥ 0.5")


class PitchCalibration(BaseModel):
    status: Literal["ok", "degraded", "manual"]
    length_m: float = 105.0
    width_m: float = 68.0


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    video_id: str
    job_id: str
    revision: int
    fps: int
    width: int
    height: int
    duration_ms: int
    frame_count: int
    tile_ms: int = TILE_MS
    tile_count: int
    tile_url_template: str = Field(
        ..., description="e.g. https://tiles.…/{tile_no}.json.gz?sig=… — {tile_no} is 6-digit")
    shots: list[Shot]
    coverage: Coverage
    teams: list[TeamInfo]
    pitch: PitchCalibration
