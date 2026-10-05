"""Playback tile format (stage S7): tiles/r{revision}/{tile_no:06d}.json.gz, one per 10 s.

Objects are positional arrays to keep tiles small:
    [track_id, cls, team, x1, y1, x2, y2, conf, px, py]
x/y are pixels on normalized.mp4; px/py are metres on the 105×68 pitch, null when the frame is
not main_wide or homography failed.
"""
from enum import IntEnum

from pydantic import BaseModel, ConfigDict, Field

from pitchvision_schemas.shots import ShotType

TILE_MS = 10_000
TILE_VERSION = 1

Obj = tuple[int, int, int, int, int, int, int, float, float | None, float | None]


class ObjCls(IntEnum):  # same codes as tracks.parquet (AD §6.5)
    player = 0
    goalkeeper = 1
    referee = 2
    ball = 3


class TileFrame(BaseModel):
    model_config = ConfigDict(extra="forbid")

    f: int = Field(..., ge=0, description="frame index on normalized video (target_fps)")
    hq: float | None = Field(None, ge=0, le=1, description="h_quality; null = no homography")
    objs: list[Obj] = Field(default_factory=list)


class Tile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    v: int = TILE_VERSION
    tile_no: int = Field(..., ge=0)
    t0_ms: int
    t1_ms: int
    fps: int
    shots: list[tuple[int, int, ShotType]] = Field(
        default_factory=list, description="[f0, f1_exclusive, type] runs covering this tile")
    frames: list[TileFrame]


def tile_no_for(ts_ms: int) -> int:
    return ts_ms // TILE_MS


def tile_key(org_id: str, video_id: str, job_id: str, revision: int, tile_no: int) -> str:
    return f"orgs/{org_id}/videos/{video_id}/jobs/{job_id}/tiles/r{revision}/{tile_no:06d}.json.gz"
