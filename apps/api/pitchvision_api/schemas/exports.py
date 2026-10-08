"""/api/v1/exports bodies (SCR-25 / SCR-26)."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pitchvision_schemas import DataFormat, DataRate, ExportKind, ExportLayers, ExportLayout
from pydantic import BaseModel, ConfigDict, Field, model_validator

from pitchvision_api.db.models import Export

# `expired`: done, but the file is gone (past expires_at) — derived, never stored
ExportState = Literal["queued", "running", "done", "failed", "cancelled", "expired"]


class ExportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    video_id: uuid.UUID
    kind: ExportKind = "video"
    from_ms: int | None = Field(None, ge=0, description="Range start; omitted = start of the video (Full match)")
    to_ms: int | None = Field(None, gt=0, description="Range end (exclusive); omitted = end of the video")
    layers: ExportLayers = ExportLayers()
    layout: ExportLayout = "video"
    quality: Literal[720, 1080] = 720
    # kind = data (SCR-25 E11): JSON document or ZIP of CSVs; tracking rows at 5 Hz or every frame (25 Hz)
    data_format: DataFormat = "json"
    rate_hz: DataRate = 5

    @model_validator(mode="after")
    def _range(self) -> "ExportCreate":
        if self.from_ms is not None and self.to_ms is not None and self.to_ms <= self.from_ms:
            raise ValueError("to_ms must be greater than from_ms")
        return self


class ExportOut(BaseModel):
    id: uuid.UUID
    video_id: uuid.UUID
    video_title: str | None = None
    kind: str
    status: ExportState
    progress: float
    filename: str
    size_bytes: int | None
    from_ms: int
    to_ms: int
    layout: str
    quality: int
    watermark: bool
    layers: dict[str, bool]
    data_format: str | None = None
    rate_hz: int | None = None
    error: dict[str, Any] | None
    created_by: uuid.UUID | None
    created_at: datetime
    finished_at: datetime | None
    expires_at: datetime | None

    @classmethod
    def of(cls, e: Export, now: datetime, video_title: str | None = None) -> "ExportOut":
        spec = e.spec or {}
        expired = e.status == "done" and e.expires_at is not None and e.expires_at <= now
        return cls(id=e.id, video_id=e.video_id, video_title=video_title, kind=e.kind,
                   status="expired" if expired else e.status,  # type: ignore[arg-type]
                   progress=float(e.progress), filename=e.filename, size_bytes=e.size_bytes,
                   from_ms=int(spec.get("from_ms", 0)), to_ms=int(spec.get("to_ms", 0)),
                   layout=str(spec.get("layout", "video")), quality=int(spec.get("out_height", 720)),
                   watermark=bool(spec.get("watermark", False)), layers=dict(spec.get("layers") or {}),
                   data_format=spec.get("data_format") if e.kind == "data" else None,
                   rate_hz=spec.get("rate_hz") if e.kind == "data" else None,
                   error=e.error, created_by=e.created_by, created_at=e.created_at, finished_at=e.finished_at,
                   expires_at=e.expires_at)  # fmt: skip


class ExportList(BaseModel):
    items: list[ExportOut]
    next_cursor: str | None = None


class ExportDownload(BaseModel):
    url: str
    filename: str
    expires_in: int
