"""/api/v1/results/{video_id}/corrections — track corrections (M2-BE-01, SCR-22; AD §8.4).

Applied optimistically by the client (on the tiles it shows), recorded here, and re-applied server-side by an
``edit`` job (tiles / positions / stats under a new revision) 30 s after the last correction of a burst."""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from pitchvision_api.schemas.jobs import JobOut


class MergeIn(BaseModel):
    """SCR-22 E03: ``from`` becomes ``into`` (the same player seen twice)."""

    type: Literal["merge"]
    from_track: int = Field(ge=0, alias="from")
    into: int = Field(ge=0)


class SplitIn(BaseModel):
    """SCR-22 E04: frames of ``track`` at / after ``at_ms`` become a new track (the server picks its id)."""

    type: Literal["split"]
    track: int = Field(ge=0)
    at_ms: int = Field(ge=0)


class RemoveIn(BaseModel):
    """SCR-22 E05 / "Not a player": drop the track in ``[from_ms, to_ms)`` (whole track when both are omitted)."""

    type: Literal["remove"]
    track: int = Field(ge=0)
    from_ms: int | None = Field(None, ge=0)
    to_ms: int | None = Field(None, gt=0)


CorrectionIn = Annotated[MergeIn | SplitIn | RemoveIn, Field(discriminator="type")]

#: Correction types that name concrete tracks of one analysis run. A new ``analyze`` / ``team_rerun`` job
#: re-tracks, so these go stale and are marked undone on re-analysis (recalibrate / repitch / edit keep them).
EDIT_TYPES: tuple[str, ...] = ("merge", "split", "remove")


class CorrectionOut(BaseModel):
    id: uuid.UUID
    type: str
    payload: dict[str, Any] = Field(description="merge {from, into} · split {track, at_ms, new_track} · "
                                                "remove {track, from_ms?, to_ms?}")
    revision: int
    undone: bool
    created_at: datetime


class CorrectionsState(BaseModel):
    items: list[CorrectionOut] = Field(description="live corrections, oldest first (apply in this order)")
    pending: bool = Field(description="corrections not in the latest results yet (an edit job will / should run)")
    job: JobOut | None = Field(None, description="the edit job that will apply them, when one is queued / running")
    apply_after: datetime | None = Field(None, description="when the queued edit job starts (debounce)")
