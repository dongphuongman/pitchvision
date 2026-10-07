"""SCR-40 Data & privacy (D02 export all data, DLG-08 counts)."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from pitchvision_api.db.models import DataExport

DataExportStatus = Literal["queued", "running", "done", "failed", "expired"]


class DataExportOut(BaseModel):
    id: uuid.UUID
    scope: Literal["org", "account"]
    status: DataExportStatus
    size_bytes: int | None
    error: dict[str, Any] | None
    created_at: datetime
    finished_at: datetime | None
    expires_at: datetime | None

    @classmethod
    def of(cls, d: DataExport, now: datetime) -> "DataExportOut":
        expired = d.status == "done" and (d.object_key is None or (d.expires_at is not None and d.expires_at <= now))
        return cls(id=d.id, scope=d.scope, status="expired" if expired else d.status,  # type: ignore[arg-type]
                   size_bytes=d.size_bytes, error=d.error, created_at=d.created_at, finished_at=d.finished_at,
                   expires_at=d.expires_at)  # fmt: skip


class PrivacySummary(BaseModel):
    """What the active org holds in this region (DLG-08 "N videos will be deleted") + the caller's latest export."""

    videos: int
    exports: int
    data_export: DataExportOut | None


class DataExportDownload(BaseModel):
    url: str
    filename: str
    expires_in: int
