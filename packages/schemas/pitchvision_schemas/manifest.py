"""Result manifest (GET /results/{id}/manifest): what the player needs before fetching tiles."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from pitchvision_schemas.shots import Shot
from pitchvision_schemas.tiles import TILE_MS

AttackSide = Literal["left", "right"]


class TeamInfo(BaseModel):
    cluster: int
    name: str
    color: str = Field(..., pattern=r"^#[0-9a-fA-F]{6}$")
    confirmed: bool = False
    attack_h1: AttackSide | None = Field(
        None, description="Side this team attacks in the first half as the main camera sees it (SCR-14 E05); "
        "null = unknown")


class TrackOverride(BaseModel):
    """A user's fix of one track (SCR-14 E06): tiles keep the detected team / class, players apply this on top."""

    track_id: int
    team: int = Field(..., ge=-1, le=1, description="cluster 0 / 1, -1 = no team (referee)")
    cls: int = Field(..., ge=0, le=2, description="ObjCls: 0 player, 1 goalkeeper, 2 referee")


class Coverage(BaseModel):
    main_wide_pct: float = Field(..., ge=0, le=100, description="% of duration with analysable wide shots")
    homography_ok_pct: float = Field(..., ge=0, le=100, description="% of main_wide frames with h_quality ≥ 0.5")


PitchStatus = Literal["ok", "degraded", "manual", "unavailable"]


class PitchCalibration(BaseModel):
    status: PitchStatus = Field(
        ..., description="ok · degraded (> 30 % of main_wide frames below h_quality 0.5) · manual (user calibrated) · "
        "unavailable (no pitch keypoint model: px/py are null, no tactical map)")
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
        ..., description="e.g. /api/v1/tiles/{video}/{job}/r0/{tile_no}.json.gz?exp=…&sig=… — {tile_no} is 6-digit. "
        "Signed, expires (re-fetch the manifest on 403). The stored manifest.json holds the object-key template.")
    shots: list[Shot]
    coverage: Coverage
    teams: list[TeamInfo]
    pitch: PitchCalibration
    track_overrides: list[TrackOverride] = Field(
        default_factory=list, description="Confirmed team / role fixes per track (served from the players table)")
