"""YouTube ingest S1 (M1-BE-08): preview metadata via the ingest worker, then create + enqueue the download.

Rejections are 422 VALIDATION_FAILED (or 502/504 INGEST_FAILED when YouTube/the worker fails) with a
machine-readable `reason` extension so the web can show a specific message:

    invalid_url · too_long · unknown_duration · live · private · members_only · needs_auth ·
    age_restricted · unavailable · blocked · probe_failed · timeout · queue_unavailable
"""

from datetime import UTC, datetime
from typing import Any

from pitchvision_schemas import ErrorCode
from sqlalchemy.ext.asyncio import AsyncSession

from pitchvision_api.auth import Principal
from pitchvision_api.db.models import Video
from pitchvision_api.errors import ApiError
from pitchvision_api.repositories import videos as repo
from pitchvision_api.schemas.videos import FromUrlIn, UrlPreview
from pitchvision_api.services import quota
from pitchvision_api.services.tasks import (
    QUEUE_INGEST,
    TASK_DOWNLOAD,
    TASK_PROBE_URL,
    TaskFailed,
    TaskQueue,
    TaskTimeout,
    TaskUnavailable,
)
from pitchvision_api.services.youtube_url import InvalidYoutubeUrl, YoutubeRef, parse_youtube_url
from pitchvision_api.settings import Settings

# reasons the user can fix by picking another video → 422; everything else is our/YouTube's failure → 502
USER_REASONS = {
    "too_long": "Video is longer than 3 hours.",
    "unknown_duration": "Video duration is unknown (live or still processing).",
    "live": "Live streams and upcoming premieres can't be analysed; wait until the stream has ended.",
    "private": "Video is private.",
    "members_only": "Video is limited to channel members or paying subscribers.",
    "needs_auth": "Video requires signing in to YouTube.",
    "age_restricted": "Video is age-restricted; we don't sign in to YouTube.",
    "unavailable": "Video is unavailable (removed, blocked in our region, or the link is wrong).",
}
BAD_AVAILABILITY = {
    "private": "private",
    "premium_only": "members_only",
    "subscriber_only": "members_only",
    "needs_auth": "needs_auth",
}
LIVE_STATUSES = {"is_live", "is_upcoming", "post_live"}


def reject(reason: str, detail: str | None = None) -> ApiError:
    if reason in USER_REASONS:
        return ApiError(
            ErrorCode.VALIDATION_FAILED,
            422,
            "Video can't be ingested",
            detail or USER_REASONS[reason],
            extensions={"reason": reason},
        )
    status = 504 if reason == "timeout" else 503 if reason == "queue_unavailable" else 502
    return ApiError(
        ErrorCode.INGEST_FAILED,
        status,
        "Could not read the video from YouTube",
        detail or "Try again in a few minutes.",
        extensions={"reason": reason},
    )


def parse(url: str) -> YoutubeRef:
    try:
        return parse_youtube_url(url)
    except InvalidYoutubeUrl as e:
        raise ApiError(
            ErrorCode.VALIDATION_FAILED, 422, "Unsupported video URL", str(e), extensions={"reason": "invalid_url"}
        ) from None


def policy_violation(info: dict[str, Any], max_duration_s: int) -> str | None:
    """S1 policy on probe metadata (the worker re-applies it before downloading)."""
    if info.get("is_live") or info.get("live_status") in LIVE_STATUSES:
        return "live"
    if (bad := BAD_AVAILABILITY.get(str(info.get("availability")))) is not None:
        return bad
    if (info.get("age_limit") or 0) >= 18:
        return "age_restricted"
    duration = info.get("duration_s")
    if duration is None:
        return "unknown_duration"
    if float(duration) > max_duration_s:
        return "too_long"
    return None


async def preview(tasks: TaskQueue, settings: Settings, url: str) -> UrlPreview:
    ref = parse(url)
    try:
        result = await tasks.call(TASK_PROBE_URL, {"url": ref.url}, QUEUE_INGEST, settings.probe_timeout_s)
    except TaskTimeout:
        raise reject("timeout", "YouTube did not answer in time; try again.") from None
    except TaskUnavailable:
        raise reject("queue_unavailable", "Ingest service unavailable; try again shortly.") from None
    except TaskFailed:
        raise reject("probe_failed") from None
    if not isinstance(result, dict):
        raise reject("probe_failed")
    if not result.get("ok"):
        raise reject(str(result.get("reason") or "probe_failed"), result.get("detail"))
    info: dict[str, Any] = result.get("info") or {}
    if (reason := policy_violation(info, settings.max_video_duration_s)) is not None:
        raise reject(reason)
    return UrlPreview(
        url=ref.url,
        video_id=ref.video_id,
        title=info.get("title"),
        duration_s=info.get("duration_s"),
        width=info.get("width"),
        height=info.get("height"),
        fps=info.get("fps"),
        is_live=bool(info.get("is_live")),
        availability=info.get("availability"),
        thumbnail_url=info.get("thumbnail_url"),
    )


async def create_from_url(session: AsyncSession, tasks: TaskQueue, principal: Principal, body: FromUrlIn) -> Video:
    ref = parse(body.url)
    await quota.ensure(session, principal)
    video = await repo.add(
        session,
        Video(
            org_id=principal.org_id,
            created_by=principal.user_id,
            source_type="youtube",
            source_uri=ref.url,
            title=body.title,
            status="ingesting",
            rights_confirmed_at=datetime.now(UTC),
        ),
    )
    # Published before our transaction commits: the worker retries briefly until the row is visible.
    # If publishing fails we raise → the row is rolled back, so no video is stuck in `ingesting`.
    try:
        await tasks.send(
            TASK_DOWNLOAD, {"video_id": str(video.id), "org_id": str(principal.org_id), "url": ref.url}, QUEUE_INGEST
        )
    except TaskUnavailable:
        raise reject("queue_unavailable", "Ingest service unavailable; try again shortly.") from None
    return video
