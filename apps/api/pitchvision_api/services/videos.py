"""Video lifecycle (W3): rename + delete. Listing/reading lives in the router (repositories.videos with latest_job).

DELETE /videos/{id} (AD §14 erasure):
1. lock the row; 404 if it doesn't exist / is already deleted (a repeated DELETE is 404 — the first one did the work)
2. cancel the active job through the same path as POST /jobs/{id}/cancel (orchestrator revokes + WS `cancelled`)
3. abort an in-progress multipart upload (best effort: the object sweep below also aborts uploads under the prefixes)
4. soft-delete: status `deleted`, deleted_at — the video disappears from lists and returns 404
5. publish `cpu.delete_video_objects(org_id, video_id)`, which deletes every object under keys.video_prefixes.
   Published before our transaction commits (same as jobs/ingest): if the broker is down we answer 503 and roll
   back, so a video is never marked deleted without its erasure queued. The worker only deletes once it sees the
   row `deleted` (it retries for a while, so a rolled-back request deletes nothing).
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime

from pitchvision_schemas import ErrorCode
from sqlalchemy.ext.asyncio import AsyncSession

from pitchvision_api.auth import Principal
from pitchvision_api.db.models import Job, Video
from pitchvision_api.errors import ApiError
from pitchvision_api.repositories import videos as repo
from pitchvision_api.services import jobs
from pitchvision_api.services.storage import STORAGE_ERRORS, Storage
from pitchvision_api.services.tasks import QUEUE_CPU, TASK_DELETE_VIDEO_OBJECTS, TaskQueue, TaskUnavailable

log = logging.getLogger(__name__)


def not_found() -> ApiError:
    return ApiError(ErrorCode.NOT_FOUND, 404, "Video not found")


async def get(session: AsyncSession, principal: Principal, video_id: uuid.UUID) -> tuple[Video, Job | None]:
    row = await repo.get_with_latest_job(session, principal.org_id, video_id)
    if row is None:
        raise not_found()
    return row


async def rename(
    session: AsyncSession, principal: Principal, video_id: uuid.UUID, title: str
) -> tuple[Video, Job | None]:
    video = await repo.get(session, principal.org_id, video_id, for_update=True)
    if video is None:
        raise not_found()
    video.title = title
    await session.flush()
    return await get(session, principal, video_id)


async def delete(
    session: AsyncSession, storage: Storage, tasks: TaskQueue, principal: Principal, video_id: uuid.UUID
) -> None:
    video = await repo.get(session, principal.org_id, video_id, for_update=True)
    if video is None:
        raise not_found()
    cancelled = await jobs.cancel_active_for_video(session, tasks, principal, video.id)
    if video.status == "uploading" and video.object_key and video.upload_id:
        try:
            await asyncio.to_thread(storage.abort_multipart, video.object_key, video.upload_id)
        except STORAGE_ERRORS as e:  # the worker's sweep aborts uploads under the video's prefixes too
            log.warning("delete %s: abort multipart failed (%r); left to the object sweep", video.id, e)
    video.status, video.upload_id, video.deleted_at = "deleted", None, datetime.now(UTC)
    await session.flush()
    try:
        await tasks.send(TASK_DELETE_VIDEO_OBJECTS, {"org_id": str(video.org_id), "video_id": str(video.id)}, QUEUE_CPU)
    except TaskUnavailable:
        raise ApiError(ErrorCode.INTERNAL, 503, "Task queue unavailable", "Try deleting again shortly.") from None
    log.info("video %s deleted (job cancelled: %s)", video.id, cancelled.id if cancelled else None)
