"""/api/v1/me: the signed-in user, SCR-06 "Change it" (delete an unverified account), SCR-40 Profile / Preferences
(PATCH), SCR-40 D03 delete account (DLG-08). Password change and "sign out other sessions" live under /auth (they
need the refresh cookie)."""

from fastapi import APIRouter, Response, status

from pitchvision_identity import schemas
from pitchvision_identity.deps import AppCtx, UserId, clear_refresh_cookie
from pitchvision_identity.services import auth as svc
from pitchvision_identity.services import erasure as erasure_svc

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



@router.get("/deletion-preview")
async def deletion_preview(user_id: UserId, ctx: AppCtx) -> schemas.DeletionPreview:
    """DLG-08: orgs that block deleting the account, and orgs that would be deleted with it."""
    return await erasure_svc.deletion_preview(ctx, user_id)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_me(body: schemas.MeDeleteIn, user_id: UserId, ctx: AppCtx) -> Response:
    """SCR-40 D03: `confirm_email` must be the account's email (422). 409 LAST_OWNER while you are the only owner
    of an org with other members; orgs with no other member are deleted too. Clears the refresh cookie."""
    await erasure_svc.delete_me(ctx, user_id, body)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_refresh_cookie(response, ctx)
    return response
