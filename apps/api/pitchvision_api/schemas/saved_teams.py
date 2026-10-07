"""SCR-40 O03 saved teams / SCR-14 E02 suggestions."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from pitchvision_api.db.models import SavedTeam
from pitchvision_api.schemas.results import HEX_COLOR


class SavedTeamOut(BaseModel):
    id: uuid.UUID
    name: str
    color: str
    uses: int
    last_used_at: datetime

    @classmethod
    def of(cls, t: SavedTeam) -> "SavedTeamOut":
        return cls(id=t.id, name=t.name, color=t.color, uses=t.uses, last_used_at=t.last_used_at)


class SavedTeamPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=40)
    color: str | None = Field(None, pattern=HEX_COLOR)

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = " ".join(v.split())
        if not v:
            raise ValueError("name is empty")
        return v
