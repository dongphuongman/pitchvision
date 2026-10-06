"""/api/v1/me: the signed-in user, SCR-06 "Change it" (delete an unverified account), SCR-40 Profile / Preferences
(PATCH). Password change and "sign out other sessions" live under /auth (they need the refresh cookie)."""

from fastapi import APIRouter, Response, status

from pitchvision_identity import schemas
from pitchvision_identity.deps import AppCtx, UserId, clear_refresh_cookie
from pitchvision_identity.services import auth as svc

router = APIRouter(prefix="/me", tags=["me"])


@router.get("")
async def get_me(user_id: UserId, ctx: AppCtx) -> schemas.Me:
    return await svc.me(ctx, user_id)


@router.delete("/unverified", status_code=status.HTTP_204_NO_CONTENT)
async def delete_unverified(user_id: UserId, ctx: AppCtx) -> Response:
    """Deletes the account if its email is not verified yet (409 CONFLICT otherwise) and clears the cookie."""
    await svc.delete_unverified(ctx, user_id)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_refresh_cookie(response, ctx)
    return response


@router.patch("")
async def patch_me(body: schemas.MePatchIn, user_id: UserId, ctx: AppCtx) -> schemas.Me:
    """SCR-40 P01 name, R03 email notifications (`email_prefs`, partial)."""
    return await svc.patch_me(ctx, user_id, body)

