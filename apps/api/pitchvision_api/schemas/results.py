"""Response bodies for /api/v1/results (M1-BE-15). The manifest itself is pitchvision_schemas.Manifest."""

import uuid

from pydantic import BaseModel, Field

from pitchvision_api.db.models import Team


class TeamOut(BaseModel):
    id: uuid.UUID
    cluster: int | None = Field(description="team-model cluster (0 / 1) the team was created from")
    name: str | None
    color: str | None = Field(description="#rrggbb (mean shirt colour of the cluster until the user changes it)")
    confirmed: bool

    @classmethod
    def of(cls, t: Team) -> "TeamOut":
        return cls(id=t.id, cluster=t.cluster, name=t.name, color=t.color, confirmed=t.confirmed)


class TeamList(BaseModel):
    items: list[TeamOut]


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
