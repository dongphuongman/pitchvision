"""Job events on Redis pub/sub `job:{id}` (AD §10.3/§10.4): the API's WebSocket hub fans them out to browsers.

Messages are `pitchvision_schemas.ws` job events (progress / completed / failed / cancelled) as JSON. Publishing is
best effort: the durable state is `jobs.status/progress/detail`, which the API also serves (GET /jobs/{id}) and the
WebSocket sends as a snapshot on subscribe — so a lost event never leaves a client wrong for long.
"""

from __future__ import annotations

import logging
from typing import Any

from pitchvision_schemas import (
    CancelledEvent,
    CompletedEvent,
    ErrorCode,
    FailedEvent,
    JobEvent,
    ProgressData,
    ProgressEvent,
)
from pitchvision_schemas.ws import CancelledData, CompletedData, FailedData, SegmentCounts

log = logging.getLogger(__name__)


def topic(job_id: object) -> str:
    return f"job:{job_id}"


def progress(job_id: object, stage: str, pct: float, *, segments: tuple[int, int, int] | None = None,
             fps: float | None = None, eta_s: int | None = None) -> ProgressEvent:  # fmt: skip
    counts = SegmentCounts(done=segments[0], running=segments[1], total=segments[2]) if segments else None
    data = ProgressData(stage=stage, pct=max(0.0, min(100.0, round(pct, 2))), fps=fps, eta_s=eta_s, segments=counts)
    return ProgressEvent(topic=topic(job_id), data=data)


def completed(job_id: object, video_id: object) -> CompletedEvent:
    return CompletedEvent(topic=topic(job_id), data=CompletedData(video_id=str(video_id)))


def failed(job_id: object, code: ErrorCode, retryable: bool) -> FailedEvent:
    return FailedEvent(topic=topic(job_id), data=FailedData(code=code, retryable=retryable))


def cancelled(job_id: object, video_id: object) -> CancelledEvent:
    return CancelledEvent(topic=topic(job_id), data=CancelledData(video_id=str(video_id)))


def publish(redis_client: Any | None, event: JobEvent) -> None:
    if redis_client is None:
        return
    try:
        redis_client.publish(event.topic, event.model_dump_json())
    except Exception as e:  # never fail a pipeline step because Redis hiccuped
        log.warning("publish %s %s failed: %r", event.type, event.topic, e)
