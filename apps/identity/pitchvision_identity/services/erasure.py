"""Deleting an org or an account (SCR-40 D03 / D04, DLG-08) and the regional purge hand-off.

Identity deletes its rows at once (FK cascades: memberships, invites, subscriptions, tokens, OAuth links) and records
an ``erasure_requests`` row in the same transaction. Regional workers pull those requests, purge videos / results /
objects / notifications, and ack per region (docs/contracts/identity-api.md "Erasure").
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from pitchvision_schemas import ErrorCode
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from pitchvision_identity import schemas
from pitchvision_identity.context import Ctx
from pitchvision_identity.db.models import (
    ErasureRequest,
    Membership,
    OAuthAccount,
    Org,
    RefreshToken,
    UpgradeRequest,  # fmt: skip
    User,
)
from pitchvision_identity.errors import ApiError
from pitchvision_identity.services.orgs import require_member


def _confirm_failed(detail: str) -> ApiError:
    return ApiError(ErrorCode.VALIDATION_FAILED, 422, "Confirmation does not match", detail)


def _ref(o: Org) -> schemas.OrgRef:
    return schemas.OrgRef(id=o.id, name=o.name, region=o.region)  # type: ignore[arg-type]


async def _erase_org(s: AsyncSession, org: Org, by: uuid.UUID) -> None:
    s.add(ErasureRequest(kind="org", subject_id=org.id, regions=[org.region], requested_by=by))
    await s.execute(update(User).where(User.last_active_org_id == org.id).values(last_active_org_id=None))
    await s.delete(org)


async def delete_org(ctx: Ctx, user_id: uuid.UUID, org_id: uuid.UUID, body: schemas.OrgDeleteIn) -> None:
    """Owner only. Every member loses access at once; regional data goes within 24 h."""
    async with ctx.sm() as s, s.begin():
        await require_member(s, org_id, user_id, "owner")
        org = await s.get(Org, org_id, with_for_update=True)
        assert org is not None
        if body.confirm_name.strip() != org.name:
            raise _confirm_failed("Type the organization name exactly as shown.")
        await _erase_org(s, org, user_id)


async def _plan(s: AsyncSession, user_id: uuid.UUID) -> tuple[list[Org], list[Org], set[str]]:
    """→ (blocking orgs, orgs deleted with the account, regions the user has data in)."""
    rows = (await s.execute(select(Membership, Org).join(Org, Org.id == Membership.org_id)
                            .where(Membership.user_id == user_id).order_by(Org.name))).all()  # fmt: skip
    blocking: list[Org] = []
    solo: list[Org] = []
    for m, o in rows:
        if m.role != "owner":
            continue
        others = (await s.execute(
            select(func.count(), func.count().filter(Membership.role == "owner"))
            .where(Membership.org_id == o.id, Membership.user_id != user_id))).one()  # fmt: skip
        if others[0] == 0:
            solo.append(o)
        elif others[1] == 0:
            blocking.append(o)
    return blocking, solo, {o.region for _, o in rows}


async def deletion_preview(ctx: Ctx, user_id: uuid.UUID) -> schemas.DeletionPreview:
    async with ctx.sm() as s:
        blocking, solo, _ = await _plan(s, user_id)
    return schemas.DeletionPreview(blocking=[_ref(o) for o in blocking], deleted_with_account=[_ref(o) for o in solo])


async def delete_me(ctx: Ctx, user_id: uuid.UUID, body: schemas.MeDeleteIn) -> None:
    """Refused (409 LAST_OWNER) while the user is the only owner of an org that has other members. Orgs where the
    user is the only member are deleted with the account."""
    async with ctx.sm() as s, s.begin():
        user = await s.get(User, user_id, with_for_update=True)
        if user is None:
            return
        if body.confirm_email.strip().lower() != user.email:
            raise _confirm_failed("Type your account's email address.")
        blocking, solo, regions = await _plan(s, user_id)
        if blocking:
            names = ", ".join(o.name for o in blocking)
            raise ApiError(ErrorCode.LAST_OWNER, 409, "Organization needs an owner",
                           f"Make someone else owner of {names} first, or delete it.")  # fmt: skip
        for o in solo:
            await s.refresh(o, with_for_update=True)
            await _erase_org(s, o, user_id)
        s.add(ErasureRequest(kind="user", subject_id=user_id, regions=sorted(regions), requested_by=user_id,
                             completed_at=None if regions else datetime.now(UTC)))  # fmt: skip
        await s.delete(user)


# ------------------------------------------------------------------ internal (regional workers)


async def pending(ctx: Ctx, region: str, limit: int) -> list[ErasureRequest]:
    async with ctx.sm() as s:
        rows = await s.scalars(
            select(ErasureRequest)
            .where(ErasureRequest.completed_at.is_(None), ErasureRequest.regions.contains([region]),
                   ~ErasureRequest.regions_done.contains([region]))
            .order_by(ErasureRequest.requested_at).limit(limit))  # fmt: skip
        return list(rows)


async def mark_done(ctx: Ctx, request_id: uuid.UUID, region: str) -> bool:
    """→ whether the request is now complete. Unknown id → 404; a region outside ``regions`` → 409."""
    async with ctx.sm() as s, s.begin():
        req = await s.get(ErasureRequest, request_id, with_for_update=True)
        if req is None:
            raise ApiError(ErrorCode.NOT_FOUND, 404, "Erasure request not found")
        if region not in req.regions:
            raise ApiError(ErrorCode.CONFLICT, 409, "Region not part of this request")
        if region not in req.regions_done:
            req.regions_done = [*req.regions_done, region]
        if req.completed_at is None and set(req.regions) <= set(req.regions_done):
            req.completed_at = datetime.now(UTC)
        return req.completed_at is not None


async def user_export(ctx: Ctx, user_id: uuid.UUID) -> dict[str, Any] | None:
    """The account part of a GDPR data export (the regional worker adds the org's data)."""
    async with ctx.sm() as s:
        user = await s.get(User, user_id)
        if user is None:
            return None
        ms = (await s.execute(select(Membership, Org).join(Org, Org.id == Membership.org_id)
                              .where(Membership.user_id == user_id))).all()  # fmt: skip
        oauth = (await s.scalars(select(OAuthAccount).where(OAuthAccount.user_id == user_id))).all()
        live = (RefreshToken.user_id == user_id, RefreshToken.used_at.is_(None), RefreshToken.revoked_at.is_(None))
        sessions = (await s.scalars(select(RefreshToken).where(*live).order_by(RefreshToken.created_at))).all()
        upgrades = (await s.scalars(select(UpgradeRequest).where(UpgradeRequest.user_id == user_id))).all()
    iso = lambda d: d.isoformat() if d else None  # noqa: E731
    return {
        "user": {"id": str(user.id), "name": user.name, "email": user.email,
                 "email_verified_at": iso(user.email_verified_at), "signup_plan": user.signup_plan,
                 "email_prefs": user.email_prefs, "created_at": iso(user.created_at),
                 "has_password": user.password_hash is not None},
        "memberships": [{"org_id": str(o.id), "org_name": o.name, "region": o.region, "role": m.role,
                         "title": m.title, "joined_at": iso(m.created_at)} for m, o in ms],
        "linked_accounts": [{"provider": a.provider, "email": a.email, "linked_at": iso(a.created_at)} for a in oauth],
        "sessions": [{"created_at": iso(t.created_at), "expires_at": iso(t.expires_at), "user_agent": t.user_agent}
                     for t in sessions],
        "upgrade_requests": [{"org_id": str(u.org_id), "plan": u.plan, "period": u.period, "reason": u.reason,
                              "created_at": iso(u.created_at)} for u in upgrades],
    }
