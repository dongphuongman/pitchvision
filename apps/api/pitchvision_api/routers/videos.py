"""/api/v1/videos — list/get/rename/delete + presigned multipart uploads (M1-BE-07, AD §8.1, §14)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse
from pitchvision_schemas import ErrorCode

from pitchvision_api.deps import CurrentPrincipal, DbSession, EditorPrincipal
from pitchvision_api.errors import ApiError, ProblemDetails, problem_response
from pitchvision_api.repositories import videos as repo
from pitchvision_api.schemas.videos import (
    SignedParts,
    SignParts,
    UploadComplete,
    UploadCreate,
    UploadCreated,
    UploadState,
    VideoOut,
    VideoPage,
    VideoUpdate,
)
from pitchvision_api.services import uploads
from pitchvision_api.services import videos as service
from pitchvision_api.services.storage import Storage
from pitchvision_api.services.tasks import TaskQueue

router = APIRouter(prefix="/videos", tags=["videos"])


def _storage(request: Request) -> Storage:
    storage: Storage = request.app.state.storage
    return storage


def _tasks(request: Request) -> TaskQueue:
    tasks: TaskQueue = request.app.state.tasks
    return tasks


@router.get("")
async def list_videos(
    principal: CurrentPrincipal,
    session: DbSession,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> VideoPage:
    try:
        after = repo.decode_cursor(cursor) if cursor else None
    except ValueError:
        raise ApiError(ErrorCode.VALIDATION_FAILED, 422, "Invalid cursor") from None
    """Newest first. Every item carries `latest_job` (its newest job, or null) from the same query."""
    rows, next_cursor = await repo.page(session, principal.org_id, limit, after)
    return VideoPage(items=[VideoOut.of(v, job) for v, job in rows], next_cursor=next_cursor)


@router.get("/{video_id}")
async def get_video(video_id: uuid.UUID, principal: CurrentPrincipal, session: DbSession) -> VideoOut:
    return VideoOut.of(*await service.get(session, principal, video_id))


@router.patch("/{video_id}")
async def update_video(
    video_id: uuid.UUID, body: VideoUpdate, principal: EditorPrincipal, session: DbSession
) -> VideoOut:
    """Rename (`title`, 1–200 characters after trimming)."""
    return VideoOut.of(*await service.rename(session, principal, video_id, body.title))


@router.delete("/{video_id}", status_code=204)
async def delete_video(
    video_id: uuid.UUID, request: Request, principal: EditorPrincipal, session: DbSession
) -> Response:
    """Delete the video: its active job is cancelled, an in-progress upload aborted, and every stored object (source,
    media, segments, results, tiles, exports) is erased in the background. The video then returns 404."""
    await service.delete(session, _storage(request), _tasks(request), principal, video_id)
    return Response(status_code=204)


@router.post("/uploads", status_code=201)
async def create_upload(
    body: UploadCreate, request: Request, principal: EditorPrincipal, session: DbSession
) -> UploadCreated:
    """Create the video + multipart upload; returns presigned PUT URLs for the first parts.

    PUT each 16 MiB slice (the last may be shorter) to its URL, keep the `ETag` response header,
    then POST /videos/{id}/uploads/complete. Do not change the part size when resuming.
    """
    return await uploads.create(session, _storage(request), request.app.state.settings, principal, body)


@router.post("/{video_id}/uploads/sign")
async def sign_parts(
    video_id: uuid.UUID, body: SignParts, request: Request, principal: EditorPrincipal, session: DbSession
) -> SignedParts:
    """Presign more part URLs (≤ 100 per call), e.g. for the next batch or when resuming."""
    storage = _storage(request)
    parts = await uploads.sign(session, storage, principal, video_id, body.part_numbers)
    return SignedParts(parts=parts, url_expires_in=storage.presign_ttl_s)


@router.get("/{video_id}/uploads")
async def get_upload(
    video_id: uuid.UUID, request: Request, principal: EditorPrincipal, session: DbSession
) -> UploadState:
    """Parts already stored (ListParts) — resume uploads only the missing ones."""
    return await uploads.state(session, _storage(request), principal, video_id)


@router.post("/{video_id}/uploads/complete", response_model=VideoOut, responses={409: {"model": ProblemDetails}})
async def complete_upload(
    video_id: uuid.UUID, body: UploadComplete, request: Request, principal: EditorPrincipal, session: DbSession
) -> VideoOut | JSONResponse:
    """Verify every part (count, 16 MiB sizes, ETags), complete the upload, check the object size → `ready`."""
    try:
        video = await uploads.complete(session, _storage(request), principal, video_id, body)
    except uploads.UploadMismatch as e:
        # returned, not raised: the request transaction must commit the `failed` status
        return problem_response(request, ErrorCode.CONFLICT, 409, "Uploaded object has the wrong size", str(e))
    return VideoOut.of(video)


@router.delete("/{video_id}/uploads", status_code=204)
async def abort_upload(
    video_id: uuid.UUID, request: Request, principal: EditorPrincipal, session: DbSession
) -> Response:
    """Abort the multipart upload and mark the video deleted."""
    await uploads.abort(session, _storage(request), principal, video_id)
    return Response(status_code=204)
