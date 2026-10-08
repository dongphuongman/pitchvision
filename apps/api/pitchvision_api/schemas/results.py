"""Bodies for /api/v1/results (M1-BE-15/16, SCR-14 teams, SCR-15 calibration). The manifest is
pitchvision_schemas.Manifest; the pitch template is pitchvision_schemas.PitchTemplateInfo."""

import uuid
from datetime import datetime
from typing import Literal

from pitchvision_schemas import AttackSide, PitchStatus
from pydantic import BaseModel, Field, field_validator, model_validator

from pitchvision_api.db.models import Team
from pitchvision_api.schemas.jobs import JobOut

HEX_COLOR = r"^#[0-9a-fA-F]{6}$"
MAX_SAMPLES_PER_TEAM = 8


class SampleCrop(BaseModel):
    """One player crop inside ``TeamList.sample_sheet`` (CSS sprite: ``background-position: -x px -y px``)."""

    x: int
    y: int
    w: int
    h: int


class SampleSheet(BaseModel):
    """S3's crop grid (``team_samples.jpg``): one row of crops per team, most typical first."""

    url: str = Field(description="presigned GET, expires (re-fetch /teams after `expires_in` s)")
    expires_in: int
    width: int
    height: int


class TeamOut(BaseModel):
    id: uuid.UUID
    cluster: int | None = Field(description="team-model cluster (0 / 1) the team was created from — the `team` value "
                                "in tiles / tracks; never changes for a video's analysis")
    name: str | None
    color: str | None = Field(description="#rrggbb (mean shirt colour of the cluster until the user changes it)")
    confirmed: bool
    attack_h1: AttackSide | None = Field(None, description="side attacked in the first half (SCR-14 E05)")
    attack_source: Literal["auto", "user"] | None = Field(None, description="auto = guessed from the goalkeepers")
    samples: list[SampleCrop] = Field(default_factory=list,
                                      description=f"≤ {MAX_SAMPLES_PER_TEAM} crops of this cluster in `sample_sheet`")

    @classmethod
    def of(cls, t: Team, samples: list[SampleCrop] | None = None) -> "TeamOut":
        return cls(id=t.id, cluster=t.cluster, name=t.name, color=t.color, confirmed=t.confirmed,
                   attack_h1=t.attack_h1, attack_source=t.attack_source, samples=samples or [])  # type: ignore[arg-type]  # fmt: skip


class TeamList(BaseModel):
    items: list[TeamOut]
    job_id: uuid.UUID | None = Field(None, description="analysis the teams / samples come from (latest done job)")
    sample_sheet: SampleSheet | None = None


class TeamEdit(BaseModel):
    cluster: int = Field(ge=0, le=1)
    name: str = Field(min_length=1, max_length=40)
    color: str = Field(pattern=HEX_COLOR)

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("name must not be blank")
        return v


class TeamsPatch(BaseModel):
    """SCR-14 E02/E03/E04/E09. ``teams`` edits are applied by cluster first; then ``swap`` exchanges name + colour
    of the two clusters. Send the final mapping with ``swap: false`` (recommended), or the pre-swap values with
    ``swap: true``. Clusters (= tile ``team`` values) never move, so no tile is regenerated."""

    teams: list[TeamEdit] = Field(default_factory=list, max_length=2)
    swap: bool = False
    confirmed: bool = True
    attack_h1: AttackSide | None = Field(None, description="E05: side team cluster 0 attacks in the first half "
                                         "(cluster 1 gets the other); omitted = unchanged")

    @model_validator(mode="after")
    def _unique(self) -> "TeamsPatch":
        if len({t.cluster for t in self.teams}) != len(self.teams):
            raise ValueError("each cluster at most once")
        return self


class SeedBox(BaseModel):
    t_ms: int = Field(ge=0, description="time of the frame on the normalized video (snapped to 25 fps)")
    box: tuple[float, float, float, float] = Field(description="[x1, y1, x2, y2] pixels on the normalized video")

    @field_validator("box")
    @classmethod
    def _box(cls, v: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        x1, y1, x2, y2 = v
        if min(v) < 0 or max(v) > 10_000 or x2 - x1 < 4 or y2 - y1 < 8:
            raise ValueError("box must be [x1, y1, x2, y2] with x2 > x1 + 4, y2 > y1 + 8, inside the frame")
        return v


class ManualTeamsIn(BaseModel):
    """SCR-14 manual mode (E11/E13): ≥ 2 players per team picked on sample frames."""

    samples: dict[Literal["0", "1"], list[SeedBox]] = Field(
        description='{"0": [{t_ms, box} ×≥2], "1": [...]} — key = the team (cluster) the player belongs to')

    @model_validator(mode="after")
    def _two_each(self) -> "ManualTeamsIn":
        for team in ("0", "1"):
            n = len(self.samples.get(team) or [])  # type: ignore[call-overload]
            if not 2 <= n <= 20:
                raise ValueError(f"team {team}: pick 2–20 players (got {n})")
        return self


class CandidateFrame(BaseModel):
    t_ms: int
    frame_idx: int
    boxes: list[tuple[int, int, int, int]] = Field(description="player candidates [x1, y1, x2, y2]")


class TeamCandidates(BaseModel):
    """Clickable player boxes on S3's sample frames (SCR-14 manual mode E10/E11); frame images via
    GET /results/{id}/calibration/frame?t_ms=."""

    job_id: uuid.UUID
    fps: float
    frames: list[CandidateFrame]


class CalibrationPoint(BaseModel):
    template_index: int = Field(ge=0, le=31, description="GET /results/pitch-template keypoint index")
    x: float = Field(ge=0, le=10_000, description="pixel on the normalized video frame")
    y: float = Field(ge=0, le=10_000)
    frame_ms: int = Field(ge=0, description="the frame the point was clicked on (all points: the same frame)")


class CalibrationIn(BaseModel):
    """SCR-15 E09. Scope ``shot``: the manual H replaces every frame of the analysed shot containing ``frame_ms``.
    ``video``: also every analysed frame of other shots whose automatic H is missing / weak."""

    points: list[CalibrationPoint] = Field(min_length=4, max_length=8)
    scope: Literal["shot", "video"] = "shot"


class CalibrationAccepted(BaseModel):
    job: JobOut = Field(description="the `recalibrate` job — progress over WS topic job:{id}, like any job")
    correction_id: uuid.UUID
    revision: int = Field(description="tiles revision the new manifest will carry")


class ManualCalibration(BaseModel):
    correction_id: uuid.UUID
    revision: int
    points: list[CalibrationPoint]
    scope: Literal["shot", "video"]
    frame_ms: int
    created_at: datetime


class CalibrationState(BaseModel):
    pitch_calibration: PitchStatus | None = Field(description="of the latest finished analysis")
    job_id: uuid.UUID | None = Field(description="latest finished analysis")
    active_job: JobOut | None = Field(description="the video's running job, if any (e.g. a recalibrate job)")
    last_manual: ManualCalibration | None = Field(description="the latest manual calibration (prefill / Reset)")


class FrameOut(BaseModel):
    url: str = Field(description="presigned GET of a JPEG of normalized.mp4 at t_ms")
    t_ms: int = Field(description="snapped to the 25 fps frame grid")
    expires_in: int


class MediaOut(BaseModel):
    video_url: str = Field(description="presigned GET of normalized.mp4 (H.264, 25 fps); re-fetch on media error")
    expires_in: int
    fps: int
    width: int
    height: int


class TrackPoint(BaseModel):
    """One 5 Hz sample (AD §9.3 positions). Pitch metres on 105 × 68; null without homography."""

    ts_ms: int
    track_id: int = Field(description="global track id (players); -1 = ball")
    team: int | None
    px: float | None
    py: float | None
    vx: float | None = Field(description="m/s")
    vy: float | None
    q: float | None = Field(description="h_quality of the frame (0..1)")


class TrackPage(BaseModel):
    items: list[TrackPoint] = Field(description="ordered by (ts_ms, track_id)")
    next_cursor: str | None = Field(description="pass as `cursor` with the same from_ms / to_ms for the next page")


PlayerRole = Literal["player", "goalkeeper", "referee"]


class PlayerOut(BaseModel):
    """A global track (M1: no ReID, so one real person may have several)."""

    track_id: int
    role: str | None
    team: int | None = Field(description="cluster 0 / 1, null = no team")
    confirmed: bool = Field(description="the role / team was set by a user (SCR-14 E06)")
    name: str | None = Field(None, description="user-assigned player name (SCR-21 Assign; OCR fills jersey)")
    jersey: int | None = Field(None, description="shirt number, manual or OCR-voted (S6)")
    jersey_conf: float | None = Field(None, description="OCR vote confidence; null = manual / unknown")
    seconds: float = Field(description="time on screen")
    t0_ms: int | None
    t1_ms: int | None


class PlayerList(BaseModel):
    items: list[PlayerOut]


class PlayerFix(BaseModel):
    track_id: int
    role: PlayerRole | None = Field(None, description="omitted = keep the current role")
    team: int | None = Field(None, ge=0, le=1, description="omitted = keep; null clears (referees always null)")
    name: str | None = Field(None, max_length=40, description="omitted = keep; null / blank clears")
    jersey: int | None = Field(None, ge=0, le=99, description="omitted = keep; null clears")



class PlayersPatch(BaseModel):
    """SCR-14 E06 "Fix assignment": display-only (tiles keep the detection; the manifest's track_overrides apply)."""

    changes: list[PlayerFix] = Field(min_length=1, max_length=200)
