"""Request/response bodies for /api/v1/videos (snake_case, ISO-8601 UTC — AD §10.1)."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from pitchvision_api.db.models import Video

VideoStatus = Literal["uploading", "ingesting", "ready", "processing", "done", "failed", "deleted"]
SourceType = Literal["upload", "youtube", "rtsp", "import"]


class VideoError(BaseModel):
    code: str
    reason: str | None = None
    detail: str | None = None


class VideoOut(BaseModel):
    id: uuid.UUID
    title: str | None
    status: VideoStatus
    source_type: SourceType | None
    source_url: str | None = Field(None, description="Original URL for YouTube sources")
    size_bytes: int | None
    duration_ms: int | None
    fps: float | None
    width: int | None
    height: int | None
    thumbnail_url: str | None
    error: VideoError | None
    rights_confirmed_at: datetime | None
    created_at: datetime

    @classmethod
    def of(cls, v: Video) -> "VideoOut":
        return cls(
            id=v.id,
            title=v.title,
            status=v.status,  # type: ignore[arg-type]
            source_type=v.source_type,  # type: ignore[arg-type]
            source_url=v.source_uri if v.source_type == "youtube" else None,
            size_bytes=v.size_bytes,
            duration_ms=v.duration_ms,
            fps=float(v.fps) if v.fps is not None else None,
            width=v.width,
            height=v.height,
            thumbnail_url=v.thumbnail_url,
            error=VideoError.model_validate(v.error) if v.error else None,
            rights_confirmed_at=v.rights_confirmed_at,
            created_at=v.created_at,
        )


class VideoPage(BaseModel):
    items: list[VideoOut]
    next_cursor: str | None = Field(None, description="Pass as ?cursor= for the next page; null = last page")


# ---------------------------------------------------------------- uploads (M1-BE-07)
class UploadCreate(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0, description="File size in bytes")
    mime: str = Field("", max_length=127, description="Browser-reported type; may be empty")
    title: str | None = Field(None, max_length=200)


class PartUrl(BaseModel):
    part_number: int
    url: str


class UploadCreated(BaseModel):
    video_id: uuid.UUID
    upload_id: str
    part_size: int = Field(description="Fixed 16 MiB; every part but the last must be exactly this size")
    part_count: int
    parts: list[PartUrl] = Field(description="Presigned PUT URLs for the first batch; sign more via /uploads/sign")
    url_expires_in: int = Field(description="Seconds until the presigned URLs expire")
    video: VideoOut


class SignParts(BaseModel):
    part_numbers: list[int] = Field(min_length=1, max_length=100)


class SignedParts(BaseModel):
    parts: list[PartUrl]
    url_expires_in: int


class StoredPartOut(BaseModel):
    part_number: int
    etag: str
    size: int


class UploadState(BaseModel):
    video_id: uuid.UUID
    upload_id: str
    part_size: int
    part_count: int
    parts: list[StoredPartOut] = Field(description="Parts already stored (resume: upload only the others)")


class CompletedPart(BaseModel):
    part_number: int = Field(ge=1, le=10_000)
    etag: str = Field(min_length=1, max_length=128)


class UploadComplete(BaseModel):
    parts: list[CompletedPart] = Field(min_length=1, max_length=10_000)


# ---------------------------------------------------------------- YouTube (M1-BE-08)
class UrlIn(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class UrlPreview(BaseModel):
    url: str = Field(description="Canonical https://www.youtube.com/watch?v=… URL that will be ingested")
    video_id: str
    title: str | None
    duration_s: float | None
    width: int | None
    height: int | None
    fps: float | None
    is_live: bool
    availability: str | None
    thumbnail_url: str | None


class FromUrlIn(UrlIn):
    title: str | None = Field(None, max_length=200)
    rights_confirmed: bool = Field(description="Caller confirms they hold the rights to analyse this video")

    @field_validator("rights_confirmed")
    @classmethod
    def _must_confirm(cls, v: bool) -> bool:
        if v is not True:
            raise ValueError("rights_confirmed must be true")
        return v


def error_json(code: str, reason: str | None, detail: str | None) -> dict[str, Any]:
    return VideoError(code=code, reason=reason, detail=detail).model_dump()
