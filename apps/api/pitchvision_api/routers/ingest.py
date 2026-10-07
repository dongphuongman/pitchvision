"""/api/v1/videos/from-url — YouTube ingest S1 (M1-BE-08). Errors carry a `reason` (see services/ingest.py)."""

from fastapi import APIRouter, Depends, Request

from pitchvision_api.deps import DbSession, EditorPrincipal
from pitchvision_api.schemas.videos import FromUrlIn, UrlIn, UrlPreview, VideoOut
from pitchvision_api.services import ingest
from pitchvision_api.services.ratelimit import INGEST, INGEST_PREVIEW, rate_limited
from pitchvision_api.services.tasks import TaskQueue

router = APIRouter(prefix="/videos", tags=["videos"])


def _tasks(request: Request) -> TaskQueue:
    tasks: TaskQueue = request.app.state.tasks
    return tasks


@router.post("/from-url/preview", dependencies=[Depends(rate_limited(INGEST_PREVIEW))])
async def preview_url(body: UrlIn, request: Request, principal: EditorPrincipal) -> UrlPreview:
    """Read title/duration/resolution of a YouTube video (≤ 20 s) and check it can be ingested.

    422 `reason`: invalid_url, too_long, unknown_duration, live, private, members_only, needs_auth,
    age_restricted, unavailable. 502/503/504 INGEST_FAILED `reason`: blocked, probe_failed, queue_unavailable, timeout.
    """
    return await ingest.preview(_tasks(request), request.app.state.settings, body.url)


@router.post("/from-url", status_code=202, dependencies=[Depends(rate_limited(INGEST))])
async def create_from_url(
    body: FromUrlIn, request: Request, principal: EditorPrincipal, session: DbSession
) -> VideoOut:
    """Create a `youtube` video in status `ingesting` and queue the download (best stream ≤ 1080p).

    Poll GET /videos/{id}: `ready` when the source is stored, `failed` with `error.reason` otherwise.
    """
    video = await ingest.create_from_url(session, _tasks(request), principal, body)
    return VideoOut.of(video)
