"""GET /api/v1/usage — the org's plan and this month's analysis minutes (top-bar meter G.E06, SCR-40 billing)."""

from fastapi import APIRouter

from pitchvision_api.deps import CurrentPrincipal, DbSession
from pitchvision_api.services import quota
from pitchvision_api.services.quota import Usage

router = APIRouter(tags=["usage"])


@router.get("/usage")
async def get_usage(principal: CurrentPrincipal, session: DbSession) -> Usage:
    """Minutes used this calendar month (UTC) by analysis jobs that are running or done, the plan's limit and when
    it resets. The plan comes from the access token (a change shows after the next token refresh)."""
    return await quota.usage(session, principal)
