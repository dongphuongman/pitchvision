"""WebSocket protocol (AD §10.3): one socket per tab, topic subscriptions, heartbeat 25 s."""
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from pitchvision_schemas.errors import ErrorCode

HEARTBEAT_S = 25


class ClientOp(BaseModel):
    op: Literal["subscribe", "unsubscribe", "ping"]
    topic: str | None = Field(None, pattern=r"^(job|live:session):[0-9a-fA-F-]{8,}$")


class SegmentCounts(BaseModel):
    done: int
    running: int
    total: int


class ProgressData(BaseModel):
    stage: str
    pct: float = Field(..., ge=0, le=100)
    fps: float | None = None
    eta_s: int | None = None
    segments: SegmentCounts | None = None


class ProgressEvent(BaseModel):
    topic: str
    type: Literal["progress"] = "progress"
    data: ProgressData


class CompletedData(BaseModel):
    video_id: str


class CompletedEvent(BaseModel):
    topic: str
    type: Literal["completed"] = "completed"
    data: CompletedData


class FailedData(BaseModel):
    code: ErrorCode
    retryable: bool


class FailedEvent(BaseModel):
    topic: str
    type: Literal["failed"] = "failed"
    data: FailedData


ServerEvent = Annotated[ProgressEvent | CompletedEvent | FailedEvent, Field(discriminator="type")]
