"""Sessions: access JWT + rotating refresh token families (AD §14), and the shared `Session` builder."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta

from pitchvision_schemas import ErrorCode, Plan, limits_for, plan_of
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from pitchvision_identity import schemas
from pitchvision_identity.context import Ctx
from pitchvision_identity.db.models import Membership, Org, RefreshToken, Subscription, User
from pitchvision_identity.errors import ApiError
from pitchvision_identity.security import AccessClaims, hash_token, new_token, utcnow


@dataclass(frozen=True)
class Issued:
    """A Session body plus the raw refresh token the router puts in the `pv_refresh` cookie."""

    session: schemas.Session
    refresh_token: str


def unauthenticated(detail: str = "Sign in again.") -> ApiError:
    return ApiError(ErrorCode.UNAUTHENTICATED, 401, "Not authenticated", detail)


def user_out(user: User) -> schemas.User:
    return schemas.User(
        id=user.id, name=user.name, email=user.email, email_verified=user.email_verified, created_at=user.created_at
    )


async def memberships_of(s: AsyncSession, user_id: uuid.UUID) -> list[tuple[Membership, Org]]:
    rows = await s.execute(
        select(Membership, Org)
        .join(Org, Org.id == Membership.org_id)
        .where(Membership.user_id == user_id)
        .order_by(Membership.created_at, Org.name)
    )
    return [(m, o) for m, o in rows]


async def org_plan(s: AsyncSession, org_id: uuid.UUID) -> tuple[Plan, int]:
    """(plan, monthly minutes) of an org: its active subscription, else Free."""
    sub = await s.get(Subscription, org_id)
    if sub is None or sub.status != "active":
        return Plan.free, limits_for(Plan.free).minutes_per_month
    plan = plan_of(sub.plan)
    return plan, limits_for(plan, sub.minutes_per_month).minutes_per_month


def membership_out(m: Membership, o: Org) -> schemas.Membership:
    return schemas.Membership(org_id=o.id, org_name=o.name, role=m.role, region=o.region)  # type: ignore[arg-type]


async def revoke_family(s: AsyncSession, family_id: uuid.UUID) -> None:
    await s.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )


async def revoke_all_for_user(s: AsyncSession, user_id: uuid.UUID) -> None:
    await s.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )


async def revoke_others(s: AsyncSession, user_id: uuid.UUID, current_raw: str | None) -> None:
    """Revoke every refresh family of the user except the one of the presented cookie."""
    keep = None
    if current_raw:
        keep = await s.scalar(select(RefreshToken.family_id).where(RefreshToken.token_hash == hash_token(current_raw),
                                                                    RefreshToken.user_id == user_id))  # fmt: skip
    stmt = update(RefreshToken).where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
    if keep is not None:
        stmt = stmt.where(RefreshToken.family_id != keep)
    await s.execute(stmt.values(revoked_at=utcnow()))


async def revoke_raw(s: AsyncSession, raw: str | None, user_id: uuid.UUID | None = None) -> None:
    """Revoke the family of a presented refresh token (optionally only if it belongs to user_id)."""
    if not raw:
        return
    row = await s.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw)))
    if row is not None and (user_id is None or row.user_id == user_id):
        await revoke_family(s, row.family_id)


async def open_session(
    s: AsyncSession,
    ctx: Ctx,
    user: User,
    prefer_org: Sequence[uuid.UUID | None] = (),
    *,
    family_id: uuid.UUID | None = None,
    user_agent: str | None = None,
) -> Issued:
    """Pick the active org (first preferred org the user still belongs to, else their first membership),
    mint an access token for it and a new refresh token (new family unless rotating)."""
    ms = await memberships_of(s, user.id)
    by_org = {o.id: (m, o) for m, o in ms}
    active: tuple[Membership, Org] | None = None
    for org_id in [*prefer_org, user.last_active_org_id]:
        if org_id is not None and org_id in by_org:
            active = by_org[org_id]
            break
    if active is None and ms:
        active = ms[0]
    org = active[1] if active else None
    if org is not None and user.last_active_org_id != org.id:
        user.last_active_org_id = org.id

    plan, quota_min = await org_plan(s, org.id) if org else (Plan.free, None)
    access, ttl = ctx.keys.issue_access_token(
        AccessClaims(
            user_id=user.id,
            email=user.email,
            email_verified=user.email_verified,
            org_id=org.id if org else None,
            role=active[0].role if active else None,
            region=org.region if org else None,
            plan=plan.value,
            quota_min=quota_min,
        )
    )
    raw = new_token()
    s.add(
        RefreshToken(
            user_id=user.id,
            family_id=family_id or uuid.uuid4(),
            token_hash=hash_token(raw),
            org_id=org.id if org else None,
            expires_at=utcnow() + timedelta(seconds=ctx.settings.refresh_token_ttl_s),
            user_agent=(user_agent or "")[:300] or None,
        )
    )
    await s.flush()
    session = schemas.Session(
        access_token=access,
        expires_in=ttl,
        user=user_out(user),
        memberships=[membership_out(m, o) for m, o in ms],
        active_org_id=org.id if org else None,
    )
    return Issued(session, raw)


async def rotate(ctx: Ctx, raw: str | None, org_id: uuid.UUID | None, user_agent: str | None) -> Issued:
    """POST /auth/refresh. Presenting an already-rotated token revokes its whole family (reuse detection)."""
    if not raw:
        raise unauthenticated("No refresh token.")
    reused = False
    async with ctx.sm() as s, s.begin():
        row = await s.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw)).with_for_update())
        if row is None or row.revoked_at is not None or row.expires_at <= utcnow():
            raise unauthenticated()
        if row.used_at is not None:
            await revoke_family(s, row.family_id)
            reused = True
        else:
            user = await s.get(User, row.user_id)
            if user is None:
                raise unauthenticated()
            if org_id is not None and not await s.get(Membership, (org_id, user.id)):
                raise ApiError(ErrorCode.FORBIDDEN, 403, "Not a member of that organization")
            row.used_at = utcnow()
            issued = await open_session(
                s, ctx, user, [org_id, row.org_id], family_id=row.family_id, user_agent=user_agent
            )
    if reused:  # committed the revocation above; now refuse
        raise unauthenticated("Refresh token reuse detected; all sessions of this login were revoked.")
    return issued


async def logout(ctx: Ctx, raw: str | None) -> None:
    async with ctx.sm() as s, s.begin():
        await revoke_raw(s, raw)
