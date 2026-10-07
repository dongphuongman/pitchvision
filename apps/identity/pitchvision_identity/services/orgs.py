"""Orgs, memberships and roles (M1-BE-06). Authorization reads memberships from the DB, never the JWT role."""

import uuid

from pitchvision_schemas import ErrorCode
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pitchvision_identity import mail, schemas
from pitchvision_identity.context import Ctx
from pitchvision_identity.db.models import Membership, Org, UpgradeRequest, User
from pitchvision_identity.errors import ApiError
from pitchvision_identity.schemas import ROLE_RANK


def org_out(o: Org) -> schemas.Org:
    return schemas.Org(id=o.id, name=o.name, kind=o.kind, region=o.region, created_at=o.created_at)  # type: ignore[arg-type]


def _member_out(m: Membership, u: User) -> schemas.Member:
    return schemas.Member(user_id=u.id, name=u.name, email=u.email, role=m.role, joined_at=m.created_at)  # type: ignore[arg-type]


def _org_not_found() -> ApiError:
    return ApiError(ErrorCode.NOT_FOUND, 404, "Organization not found")


def _forbidden(detail: str) -> ApiError:
    return ApiError(ErrorCode.FORBIDDEN, 403, "Not allowed", detail)


def _last_owner() -> ApiError:
    return ApiError(ErrorCode.LAST_OWNER, 409, "Organization needs an owner", "Make someone else owner first.")


async def require_member(
    s: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, min_role: str = "viewer"
) -> Membership:
    """Non-members get 404 (org existence is not revealed); members below min_role get 403."""
    m = await s.get(Membership, (org_id, user_id))
    if m is None:
        raise _org_not_found()
    if ROLE_RANK[m.role] < ROLE_RANK[min_role]:
        raise _forbidden(f"Requires the {min_role} role or higher.")
    return m


async def create(ctx: Ctx, user_id: uuid.UUID, body: schemas.OrgCreateIn) -> schemas.Org:
    async with ctx.sm() as s, s.begin():
        user = await s.get(User, user_id)
        if user is None:
            raise ApiError(ErrorCode.UNAUTHENTICATED, 401, "Not authenticated")
        if not user.email_verified:
            raise ApiError(ErrorCode.EMAIL_NOT_VERIFIED, 403, "Verify your email first")
        if body.region not in ctx.settings.regions:
            raise ApiError(ErrorCode.VALIDATION_FAILED, 422, "Region not available",
                           f"This server stores data in: {', '.join(ctx.settings.regions)}.")  # fmt: skip
        org = Org(name=body.name, kind=body.kind, region=body.region, created_by=user.id)
        s.add(org)
        await s.flush()
        s.add(Membership(org_id=org.id, user_id=user.id, role="owner", title=body.my_role_title or None))
        user.last_active_org_id = org.id  # the next /auth/refresh lands in the new org
        await s.flush()
        return org_out(org)


async def get(ctx: Ctx, user_id: uuid.UUID, org_id: uuid.UUID) -> schemas.Org:
    async with ctx.sm() as s:
        await require_member(s, org_id, user_id)
        org = await s.get(Org, org_id)
        assert org is not None
        return org_out(org)


async def patch(ctx: Ctx, user_id: uuid.UUID, org_id: uuid.UUID, body: schemas.OrgPatchIn) -> schemas.Org:
    async with ctx.sm() as s, s.begin():
        await require_member(s, org_id, user_id, "admin")
        org = await s.get(Org, org_id, with_for_update=True)
        assert org is not None
        if body.name is not None:
            org.name = body.name
        await s.flush()
        return org_out(org)


async def members(ctx: Ctx, user_id: uuid.UUID, org_id: uuid.UUID) -> list[schemas.Member]:
    async with ctx.sm() as s:
        await require_member(s, org_id, user_id, "admin")
        rows = await s.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.org_id == org_id)
            .order_by(Membership.created_at, User.name)
        )
        return [_member_out(m, u) for m, u in rows]


async def _lock_target(s: AsyncSession, org_id: uuid.UUID, target_id: uuid.UUID) -> Membership:
    # Lock the org row first: every owner-count change serialises on it (no two demotions racing past the check).
    await s.get(Org, org_id, with_for_update=True)
    target = await s.get(Membership, (org_id, target_id), with_for_update=True)
    if target is None:
        raise ApiError(ErrorCode.NOT_FOUND, 404, "Member not found")
    return target


async def _other_owners(s: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID) -> int:
    n = await s.scalar(
        select(func.count())
        .select_from(Membership)
        .where(Membership.org_id == org_id, Membership.role == "owner", Membership.user_id != user_id)
    )
    return int(n or 0)


async def set_role(
    ctx: Ctx, actor_id: uuid.UUID, org_id: uuid.UUID, target_id: uuid.UUID, body: schemas.MemberPatchIn
) -> schemas.Member:
    """Admins manage viewer/editor/admin; only owners grant or take away `owner`. The last owner stays owner."""
    async with ctx.sm() as s, s.begin():
        actor = await require_member(s, org_id, actor_id, "admin")
        target = await _lock_target(s, org_id, target_id)
        touches_owner = target.role == "owner" or body.role == "owner"
        if touches_owner and actor.role != "owner":
            raise _forbidden("Only an owner can grant or change the owner role.")
        if target.role == "owner" and body.role != "owner" and await _other_owners(s, org_id, target_id) == 0:
            raise _last_owner()
        target.role = body.role
        await s.flush()
        user = await s.get(User, target_id)
        assert user is not None
        return _member_out(target, user)


async def remove(ctx: Ctx, actor_id: uuid.UUID, org_id: uuid.UUID, target_id: uuid.UUID) -> None:
    """Admin+ removes members; anyone may remove themselves (leave). Owners are removed only by owners."""
    async with ctx.sm() as s, s.begin():
        actor = await require_member(s, org_id, actor_id, "viewer" if actor_id == target_id else "admin")
        target = await _lock_target(s, org_id, target_id)
        if target.role == "owner" and actor.role != "owner":
            raise _forbidden("Only an owner can remove an owner.")
        if target.role == "owner" and await _other_owners(s, org_id, target_id) == 0:
            raise _last_owner()
        await s.delete(target)


async def request_upgrade(ctx: Ctx, user_id: uuid.UUID, org_id: uuid.UUID, body: schemas.UpgradeRequestIn
                          ) -> schemas.UpgradeRequestOut:  # fmt: skip
    """SCR-41 waitlist (no billing yet): record the request; a non-owner's request emails every owner (E07)."""
    async with ctx.sm() as s, s.begin():
        me = await require_member(s, org_id, user_id)
        org = await s.get(Org, org_id)
        user = await s.get(User, user_id)
        owners = [] if me.role == "owner" else list((await s.execute(
            select(User).join(Membership, Membership.user_id == User.id)
            .where(Membership.org_id == org_id, Membership.role == "owner"))).scalars())  # fmt: skip
        row = UpgradeRequest(org_id=org_id, user_id=user_id, plan=body.plan, period=body.period, reason=body.reason,
                             owners_notified=len(owners))  # fmt: skip
        s.add(row)
        await s.flush()
        out = schemas.UpgradeRequestOut(id=row.id, plan=body.plan, period=body.period,
                                        owners_notified=len(owners), created_at=row.created_at)  # fmt: skip
    for o in owners:
        msg = mail.upgrade_request_email(ctx.settings.web_url, o.email, o.name, user.name if user else "A member",
                                         org.name if org else "your organization", body.plan, body.reason)  # fmt: skip
        await mail.send_quietly(ctx.mailer, msg)
    return out
