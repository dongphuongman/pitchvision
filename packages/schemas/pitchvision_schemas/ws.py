"""WebSocket protocol (AD §10.3): one socket per tab, topic subscriptions, heartbeat 25 s.

server → client frames are all `ServerEvent` (discriminated on `type`):
- job events on topic `job:{id}` (Redis pub/sub from the workers, fanned out by the API):
  `progress` · `completed` · `failed` · `cancelled`
- control messages from the API's socket session:
  `subscribed {topic, data: {status}}` (followed by a snapshot job event of the current state) · `unsubscribed {topic}`
  · `ping {ts}` (server heartbeat) · `pong` (answer to the client's ping) · `error {topic?, data: {code, detail?}}`
"""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from pitchvision_schemas.errors import ErrorCode
from pitchvision_schemas.jobs import JobStatus

HEARTBEAT_S = 25

# TS: defaults (the `type` literal) are required in the serialization schema, so generated types narrow on `type`
_EVENT = ConfigDict(json_schema_serialization_defaults_required=True)


class ClientOp(BaseModel):
    op: Literal["subscribe", "unsubscribe", "ping"]
    topic: str | None = Field(None, pattern=r"^(job|live:session):[0-9a-fA-F-]{8,}$")


# ---------------------------------------------------------------- job events (topic job:{id})
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
    model_config = _EVENT

    topic: str
    type: Literal["progress"] = "progress"
    data: ProgressData


class CompletedData(BaseModel):
    video_id: str


class CompletedEvent(BaseModel):
    model_config = _EVENT

    topic: str
    type: Literal["completed"] = "completed"
    data: CompletedData


class FailedData(BaseModel):
    code: ErrorCode
    retryable: bool


class FailedEvent(BaseModel):
    model_config = _EVENT

    topic: str
    type: Literal["failed"] = "failed"
    data: FailedData


class CancelledData(BaseModel):
    video_id: str


class CancelledEvent(BaseModel):
    """The job was cancelled (POST /jobs/{id}/cancel or the video was deleted); no further events follow."""

    model_config = _EVENT

    topic: str
    type: Literal["cancelled"] = "cancelled"
    data: CancelledData


JobEvent = ProgressEvent | CompletedEvent | FailedEvent | CancelledEvent


# ---------------------------------------------------------------- control messages (API socket session)
class SubscribedData(BaseModel):
    status: JobStatus


class SubscribedMessage(BaseModel):
    model_config = _EVENT

    topic: str
    type: Literal["subscribed"] = "subscribed"
    data: SubscribedData


class UnsubscribedMessage(BaseModel):
    model_config = _EVENT

    topic: str
    type: Literal["unsubscribed"] = "unsubscribed"


class PingMessage(BaseModel):
    """Server heartbeat every HEARTBEAT_S seconds."""

    model_config = _EVENT

    type: Literal["ping"] = "ping"
    ts: int = Field(..., description="server time, Unix seconds")


class PongMessage(BaseModel):
    model_config = _EVENT

    type: Literal["pong"] = "pong"


class ErrorData(BaseModel):
    code: ErrorCode = Field(..., description="VALIDATION_FAILED · NOT_FOUND (unknown job or another org's) · "
                                             "RATE_LIMITED")
    detail: str | None = None


class ErrorMessage(BaseModel):
    model_config = _EVENT

    type: Literal["error"] = "error"
    topic: str | None = Field(None, description="the topic the failed op referred to, if any")
    data: ErrorData


ControlMessage = SubscribedMessage | UnsubscribedMessage | PingMessage | PongMessage | ErrorMessage

ServerEvent = Annotated[
    ProgressEvent
    | CompletedEvent
    | FailedEvent
    | CancelledEvent
    | SubscribedMessage
    | UnsubscribedMessage
    | PingMessage
    | PongMessage
    | ErrorMessage,
    Field(discriminator="type"),
]
