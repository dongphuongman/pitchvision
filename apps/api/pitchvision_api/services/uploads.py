"""Browser → object storage multipart uploads (M1-BE-07, AD §8.1). The file never passes through the API:
we create the multipart upload, hand out presigned part URLs in batches, and verify + complete at the end."""

import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import PurePosixPath, PureWindowsPath

from pitchvision_schemas import ErrorCode
from sqlalchemy.ext.asyncio import AsyncSession

from pitchvision_api.auth import Principal
from pitchvision_api.db.models import Video
from pitchvision_api.errors import ApiError
from pitchvision_api.repositories import videos as repo
from pitchvision_api.schemas.videos import (
    PartUrl,
    StoredPartOut,
    UploadComplete,
    UploadCreate,
    UploadCreated,
    UploadState,
    VideoOut,
    error_json,
)
from pitchvision_api.services.storage import (
    MAX_PARTS,
    PART_SIZE,
    STORAGE_ERRORS,
    Storage,
    expected_part_size,
    normalize_etag,
    part_count,
    source_key,
    storage_error,
)
from pitchvision_api.settings import Settings

FIRST_BATCH = 32  # SP-07: sign in batches so URLs never outlive a slow upload
SIGN_MAX = 100

# extension -> canonical Content-Type stored on the object (AD §14: only known containers)
CONTAINERS: dict[str, str] = {
    "mp4": "video/mp4",
    "m4v": "video/x-m4v",
    "mov": "video/quicktime",
    "mkv": "video/x-matroska",
    "webm": "video/webm",
    "avi": "video/x-msvideo",
    "ts": "video/mp2t",
    "mts": "video/mp2t",
    "m2ts": "video/mp2t",
}
GENERIC_MIMES = {"", "application/octet-stream", "binary/octet-stream"}
ALLOWED_MIMES = {*CONTAINERS.values(), "video/avi", "video/msvideo", *GENERIC_MIMES}


async def _s3[T](fn: Callable[..., T], *args: object) -> T:
    try:
        return await asyncio.to_thread(fn, *args)
    except STORAGE_ERRORS as e:
        raise storage_error(e) from None


def _invalid(detail: str) -> ApiError:
    return ApiError(ErrorCode.VALIDATION_FAILED, 422, "Invalid upload", detail)


def container_of(filename: str, mime: str) -> tuple[str, str]:
    """(extension, content type) or 422. The extension decides; the browser's mime must not contradict it."""
    name = PureWindowsPath(PurePosixPath(filename).name).name  # strip any client path, both separators
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in CONTAINERS:
        raise _invalid(f"Unsupported file type {ext or '(none)'!r}; allowed: {', '.join(sorted(CONTAINERS))}.")
    if mime.strip().lower() not in ALLOWED_MIMES:
        raise _invalid(f"Unsupported content type {mime!r} for a video upload.")
    return ext, CONTAINERS[ext]


async def create(
    session: AsyncSession, storage: Storage, settings: Settings, principal: Principal, body: UploadCreate
) -> UploadCreated:
    ext, content_type = container_of(body.filename, body.mime)
    if body.size > settings.max_upload_bytes:
        raise _invalid(f"File is {body.size} bytes; the limit is {settings.max_upload_bytes} bytes.")
    n_parts = part_count(body.size)
    if n_parts > MAX_PARTS:
        raise _invalid("File needs more than 10000 parts of 16 MiB.")
    title = body.title or PurePosixPath(PureWindowsPath(body.filename).name).stem[:200] or None
    video = await repo.add(
        session,
        Video(
            org_id=principal.org_id,
            created_by=principal.user_id,
            source_type="upload",
            title=title,
            status="uploading",
            size_bytes=body.size,
        ),
    )
    key = source_key(principal.org_id, video.id, ext)
    upload_id = await _s3(storage.create_multipart, key, content_type)
    video.object_key, video.upload_id = key, upload_id
    await session.flush()
    urls = storage.presign_parts(key, upload_id, list(range(1, min(n_parts, FIRST_BATCH) + 1)))
    return UploadCreated(
        video_id=video.id,
        upload_id=upload_id,
        part_size=PART_SIZE,
        part_count=n_parts,
        parts=[PartUrl(part_number=n, url=u) for n, u in urls.items()],
        url_expires_in=storage.presign_ttl_s,
        video=VideoOut.of(video),
    )


async def _uploading(session: AsyncSession, principal: Principal, video_id: uuid.UUID, *, lock: bool = False) -> Video:
    video = await repo.get(session, principal.org_id, video_id, for_update=lock)
    if video is None:
        raise ApiError(ErrorCode.NOT_FOUND, 404, "Video not found")
    if video.status != "uploading" or not video.upload_id or not video.object_key or video.size_bytes is None:
        raise ApiError(ErrorCode.CONFLICT, 409, "No upload in progress", f"Video status is {video.status!r}.")
    return video


async def sign(
    session: AsyncSession, storage: Storage, principal: Principal, video_id: uuid.UUID, numbers: list[int]
) -> list[PartUrl]:
    video = await _uploading(session, principal, video_id)
    assert video.object_key and video.upload_id and video.size_bytes is not None
    n_parts = part_count(video.size_bytes)
    bad = sorted({n for n in numbers if not 1 <= n <= n_parts})
    if bad:
        raise _invalid(f"Part numbers must be in 1..{n_parts}; got {bad[:10]}.")
    urls = storage.presign_parts(video.object_key, video.upload_id, sorted(set(numbers)))
    return [PartUrl(part_number=n, url=u) for n, u in urls.items()]


async def state(session: AsyncSession, storage: Storage, principal: Principal, video_id: uuid.UUID) -> UploadState:
    video = await _uploading(session, principal, video_id)
    assert video.object_key and video.upload_id and video.size_bytes is not None
    parts = await _s3(storage.list_parts, video.object_key, video.upload_id)
    return UploadState(
        video_id=video.id,
        upload_id=video.upload_id,
        part_size=PART_SIZE,
        part_count=part_count(video.size_bytes),
        parts=[StoredPartOut(part_number=p.part_number, etag=p.etag, size=p.size) for p in parts],
    )


class UploadMismatch(Exception):
    """Stored object disagrees with the declared size after completion (video marked failed)."""


async def complete(
    session: AsyncSession, storage: Storage, principal: Principal, video_id: uuid.UUID, body: UploadComplete
) -> Video:
    video = await repo.get(session, principal.org_id, video_id, for_update=True)
    if video is None:
        raise ApiError(ErrorCode.NOT_FOUND, 404, "Video not found")
    if video.status == "ready" and video.source_type == "upload":
        return video  # retried complete after a lost response: idempotent
    video = await _uploading(session, principal, video_id)
    assert video.object_key and video.upload_id and video.size_bytes is not None
    size, n_parts = video.size_bytes, part_count(video.size_bytes)

    numbers = [p.part_number for p in body.parts]
    if sorted(numbers) != list(range(1, n_parts + 1)):
        raise _invalid(f"Send every part 1..{n_parts} exactly once (got {len(numbers)} entries).")

    # Verify against storage before completing: R2 enforces equal part sizes itself, other S3 servers don't,
    # and a wrong-sized part would silently corrupt the source we analyse.
    stored = {p.part_number: p for p in await _s3(storage.list_parts, video.object_key, video.upload_id)}
    problems: list[str] = []
    for p in sorted(body.parts, key=lambda x: x.part_number):
        s = stored.get(p.part_number)
        want = expected_part_size(p.part_number, size)
        if s is None:
            problems.append(f"part {p.part_number} missing")
        elif s.size != want:
            problems.append(f"part {p.part_number} is {s.size} bytes, expected {want}")
        elif s.etag != normalize_etag(p.etag):
            problems.append(f"part {p.part_number} etag mismatch")
    if problems:
        detail = "; ".join(problems[:5]) + (f" (+{len(problems) - 5} more)" if len(problems) > 5 else "")
        raise ApiError(ErrorCode.CONFLICT, 409, "Upload incomplete", detail + ". Re-upload those parts, then retry.")

    parts = [(p.part_number, stored[p.part_number].etag) for p in body.parts]
    await _s3(storage.complete_multipart, video.object_key, video.upload_id, sorted(parts))
    actual = await _s3(storage.head_size, video.object_key)
    video.upload_id = None
    if actual != size:
        await _s3(storage.delete_object, video.object_key)
        video.status = "failed"
        video.error = error_json("VALIDATION_FAILED", "size_mismatch", f"stored {actual} bytes, declared {size}")
        await session.flush()
        raise UploadMismatch(video.error["detail"])
    video.status = "ready"
    # TODO(W3, M1-BE-09): auto_analyze → create the job and enqueue it to the orchestrator here.
    await session.flush()
    return video


async def abort(session: AsyncSession, storage: Storage, principal: Principal, video_id: uuid.UUID) -> None:
    video = await repo.get(session, principal.org_id, video_id, for_update=True)
    if video is None:
        raise ApiError(ErrorCode.NOT_FOUND, 404, "Video not found")
    if video.status not in ("uploading", "failed") or video.source_type != "upload":
        raise ApiError(ErrorCode.CONFLICT, 409, "No upload to cancel", f"Video status is {video.status!r}.")
    if video.object_key and video.upload_id:
        await _s3(storage.abort_multipart, video.object_key, video.upload_id)
    if video.object_key and video.status == "failed":
        await _s3(storage.delete_object, video.object_key)
    video.status, video.upload_id, video.deleted_at = "deleted", None, datetime.now(UTC)
    await session.flush()
