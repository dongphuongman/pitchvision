"""/api/v1/orgs*: create (region is chosen here, once), read/rename, members + roles, invites (admin+)."""

import uuid

from fastapi import APIRouter, Response, status

from pitchvision_identity import schemas
from pitchvision_identity.deps import AppCtx, UserId
from pitchvision_identity.services import erasure as erasure_svc
from pitchvision_identity.services import invites as invite_svc
from pitchvision_identity.services import orgs as svc

router = APIRouter(prefix="/orgs", tags=["orgs"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_org(body: schemas.OrgCreateIn, user_id: UserId, ctx: AppCtx) -> schemas.Org:
    """Creator becomes owner (verified email required: 403 EMAIL_NOT_VERIFIED). POST /auth/refresh
    `{org_id}` afterwards to get a token for it."""
    return await svc.create(ctx, user_id, body)


@router.get("/{org_id}")
async def get_org(org_id: uuid.UUID, user_id: UserId, ctx: AppCtx) -> schemas.Org:
    """Members only (non-members get 404)."""
    return await svc.get(ctx, user_id, org_id)


@router.patch("/{org_id}")
async def patch_org(org_id: uuid.UUID, body: schemas.OrgPatchIn, user_id: UserId, ctx: AppCtx) -> schemas.Org:
    """Admin+. Region is immutable."""
    return await svc.patch(ctx, user_id, org_id, body)


@router.delete("/{org_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_org(org_id: uuid.UUID, body: schemas.OrgDeleteIn, user_id: UserId, ctx: AppCtx) -> Response:
    """SCR-40 D04 / DLG-08, owner only: `confirm_name` must equal the org's name (422 otherwise). Members lose
    access at once; videos, results and files in the org's region are purged within 24 h."""
    await erasure_svc.delete_org(ctx, user_id, org_id, body)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{org_id}/members")
async def list_members(org_id: uuid.UUID, user_id: UserId, ctx: AppCtx) -> list[schemas.Member]:
    return await svc.members(ctx, user_id, org_id)


@router.patch("/{org_id}/members/{member_id}")
async def set_member_role(
    org_id: uuid.UUID, member_id: uuid.UUID, body: schemas.MemberPatchIn, user_id: UserId, ctx: AppCtx
) -> schemas.Member:
    """Admin+; only owners grant/revoke `owner`; the last owner cannot be demoted (409 LAST_OWNER)."""
    return await svc.set_role(ctx, user_id, org_id, member_id, body)


@router.delete("/{org_id}/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(org_id: uuid.UUID, member_id: uuid.UUID, user_id: UserId, ctx: AppCtx) -> Response:
    """Admin+ (or yourself, to leave); same last-owner rule."""
    await svc.remove(ctx, user_id, org_id, member_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{org_id}/invites", status_code=status.HTTP_201_CREATED)
async def create_invites(
    org_id: uuid.UUID, body: schemas.InvitesIn, user_id: UserId, ctx: AppCtx
) -> list[schemas.Invite]:
    """Admin+; 7-day links. Emails that are already members are skipped; a pending invite to the same email
    is replaced (old link stops working)."""
    return await invite_svc.create(ctx, user_id, org_id, body)


@router.get("/{org_id}/invites")
async def list_invites(org_id: uuid.UUID, user_id: UserId, ctx: AppCtx) -> list[schemas.Invite]:
    """Admin+; pending (not accepted, not revoked) — expired ones included, check `expires_at`."""
    return await invite_svc.list_pending(ctx, user_id, org_id)


@router.post("/{org_id}/invites/{invite_id}/resend", status_code=status.HTTP_202_ACCEPTED)
async def resend_invite(org_id: uuid.UUID, invite_id: uuid.UUID, user_id: UserId, ctx: AppCtx) -> Response:
    """New link + new 7-day expiry; the previous link stops working."""
    await invite_svc.resend(ctx, user_id, org_id, invite_id)
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.delete("/{org_id}/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invite(org_id: uuid.UUID, invite_id: uuid.UUID, user_id: UserId, ctx: AppCtx) -> Response:
    await invite_svc.revoke(ctx, user_id, org_id, invite_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{org_id}/upgrade-requests", status_code=202)
async def request_upgrade(org_id: uuid.UUID, body: schemas.UpgradeRequestIn, user_id: UserId, ctx: AppCtx
                          ) -> schemas.UpgradeRequestOut:  # fmt: skip
    """SCR-41 while billing is not live: join the waitlist for a plan. Any member; a non-owner's request emails
    the owners (E07 "Notify owner")."""
    return await svc.request_upgrade(ctx, user_id, org_id, body)
