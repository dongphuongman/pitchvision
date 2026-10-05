"""/api/v1/invites/{token}: public preview (SCR-52) and accept (signed in, email must match)."""

from typing import Annotated

from fastapi import APIRouter, Path, Request, Response

from pitchvision_identity import schemas
from pitchvision_identity.deps import AppCtx, UserId, set_refresh_cookie, user_agent
from pitchvision_identity.services import invites as svc

router = APIRouter(prefix="/invites", tags=["invites"])

InviteToken = Annotated[str, Path(min_length=10, max_length=256)]


@router.get("/{token}")
async def preview_invite(token: InviteToken, ctx: AppCtx) -> schemas.InvitePreview:
    """Public. Unknown/revoked -> 404; accepted or past expiry -> `expired: true`."""
    return await svc.preview(ctx, token)


@router.post("/{token}/accept")
async def accept_invite(
    token: InviteToken, request: Request, response: Response, user_id: UserId, ctx: AppCtx
) -> schemas.Session:
    """Joins the org and returns a Session whose active org is the invited one.
    Wrong account -> 403 INVITE_EMAIL_MISMATCH; expired/used -> 410 TOKEN_EXPIRED; unknown/revoked -> 404."""
    issued = await svc.accept(ctx, user_id, token, user_agent(request))
    set_refresh_cookie(response, ctx, issued.refresh_token)
    response.headers["Cache-Control"] = "no-store"
    return issued.session
