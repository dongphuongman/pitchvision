"""Exports (SCR-25 / SCR-26, M1-BE-17, AD S11): what the API freezes when an export is requested and the worker
renders (``cpu.render_export``). Everything the render needs is in ``ExportSpec`` — the worker never re-reads the
manifest or the teams table, so a rename after "Start export" doesn't change a running export.

``kind = video`` (M1) renders the annotated MP4; ``kind = data`` (M3-BE-03, SCR-25 tab Data / SCR-20f) writes the
raw tracking + stats as JSON or a ZIP of CSVs, every tracking row flagged with its ``shot_type`` and whether it is
``estimated`` (interpolated). PDF (M4) and the tactical-map video (M2) come later (UIUX SCR-25 E02–E05).
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pitchvision_schemas.manifest import PitchStatus, TeamInfo, TrackOverride
from pitchvision_schemas.shots import Shot

ExportKind = Literal["video", "data"]
DataFormat = Literal["json", "csv"]
DataRate = Literal[5, 25]
ExportStatus = Literal["queued", "running", "done", "failed", "cancelled"]
ExportLayout = Literal["video", "video_map"]
EXPORT_HEIGHTS = (720, 1080)
TERMINAL_EXPORT: tuple[ExportStatus, ...] = ("done", "failed", "cancelled")


class ExportLayers(BaseModel):
    """SCR-25 E07 — defaults follow the workspace's overlay toggles (web)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    boxes: bool = True
    labels: bool = True
    conf: bool = False
    ball: bool = True
    trails: bool = False


class ExportSpec(BaseModel):
    """The frozen render request (``exports.spec``)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: ExportKind = "video"
    job_id: str                      # the analysis whose tracks are drawn (latest done job at request time)
    revision: int
    from_ms: int = Field(..., ge=0)  # half-open [from_ms, to_ms) on the normalized timeline
    to_ms: int
    fps: int = 25
    width: int                       # normalized.mp4 size (overlay pixel space)
    height: int
    out_height: Literal[720, 1080] = 720
    layout: ExportLayout = "video"
    layers: ExportLayers = ExportLayers()
    watermark: bool = True
    ttl_days: int = Field(7, ge=1)   # the file is deleted this long after it is done (plan's export_ttl_days)
    teams: list[TeamInfo] = Field(default_factory=list)
    track_overrides: list[TrackOverride] = Field(default_factory=list)  # SCR-14 E06 fixes, drawn like the player
    pitch_status: PitchStatus = "unavailable"
    # kind = data (SCR-25 E11): format + sampling rate, the shots (→ each row's shot_type) and the stats as the user
    # saw them when exporting (GET /results/{v}/stats + /passes), so the file matches the screen
    data_format: DataFormat = "json"
    rate_hz: DataRate = 5
    video_title: str | None = None
    duration_ms: int | None = None
    shots: list[Shot] = Field(default_factory=list)
    stats: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _range(self) -> "ExportSpec":
        if self.to_ms <= self.from_ms:
            raise ValueError("to_ms must be > from_ms")
        return self
