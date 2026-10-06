"""/api/v1/notifications — SCR-51 notification center: the caller's notifications in the active org."""

from typing import Annotated

from fastapi import APIRouter, Query, Response

from pitchvision_api.deps import CurrentPrincipal, DbSession
from pitchvision_api.schemas.notifications import MarkRead, NotificationList
from pitchvision_api.services import notifications as service

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
async def list_notifications(principal: CurrentPrincipal, session: DbSession,
                             limit: Annotated[int, Query(ge=1, le=100)] = 20,
                             cursor: Annotated[str | None, Query()] = None) -> NotificationList:  # fmt: skip
    """Newest first + `unread_count` (bell badge G.E07). `path` is the web route to open; the text is rendered by
    the web from `type` + `data` + `video_title`."""
    return await service.page(session, principal, limit, cursor)


@router.post("/read", status_code=204)
async def mark_read(body: MarkRead, principal: CurrentPrincipal, session: DbSession) -> Response:
    """Mark `ids` (or `all: true`) as read."""
    await service.mark_read(session, principal, body)
    return Response(status_code=204)
