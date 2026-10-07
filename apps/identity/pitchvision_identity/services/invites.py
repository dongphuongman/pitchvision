"""Invites (M1-BE-06): create/list/resend/revoke (admin+), public preview, accept, register-with-invite."""

import uuid
from datetime import timedelta

from pitchvision_schemas import ErrorCode
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from pitchvision_identity import mail, schemas
from pitchvision_identity.context import Ctx
from pitchvision_identity.db.models import Invite, MemberEvent, Membership, Org, User
from pitchvision_identity.errors import ApiError
from pitchvision_identity.mail import EmailMessage
from pitchvision_identity.security import hash_token, new_token, utcnow
from pitchvision_identity.services.orgs import require_member
from pitchvision_identity.services.sessions import Issued, open_session, unauthenticated

INVITE_TTL = timedelta(days=7)


def email_mismatch() -> ApiError:
    return ApiError(
        ErrorCode.INVITE_EMAIL_MISMATCH, 403, "Invitation is for another email", "Sign in with the invited account."
    )


def _not_found() -> ApiError:
    return ApiError(ErrorCode.NOT_FOUND, 404, "Invitation not found")


def _expired() -> ApiError:
    return ApiError(ErrorCode.TOKEN_EXPIRED, 410, "Invitation expired or already used", "Ask for a new invite.")


def invite_out(i: Invite) -> schemas.Invite:
    return schemas.Invite(
        id=i.id,
        email=i.email,
        role=i.role,
        expires_at=i.expires_at,
        created_at=i.created_at,
        invited_by=i.invited_by,  # type: ignore[arg-type]
    )


PENDING = (Invite.accepted_at.is_(None), Invite.revoked_at.is_(None))


async def _by_token(s: AsyncSession, raw: str, *, lock: bool = False) -> Invite | None:
    q = select(Invite).where(Invite.token_hash == hash_token(raw))
    return await s.scalar(q.with_for_update() if lock else q)


async def load_acceptable(s: AsyncSession, raw: str) -> Invite:
    """Locked invite that can still be accepted: unknown/revoked -> 404, used/expired -> 410."""
    inv = await _by_token(s, raw, lock=True)
    if inv is None or inv.revoked_at is not None:
        raise _not_found()
    if inv.accepted_at is not None or inv.expires_at <= utcnow():
        raise _expired()
    return inv


async def accept_rows(s: AsyncSession, inv: Invite, user: User) -> None:
    """Membership (keeps an existing one as is) + a member_events row for the org's admins, invite consumed, email
    proven by the link."""
    now = utcnow()
    if await s.get(Membership, (inv.org_id, user.id)) is None:
        s.add(Membership(org_id=inv.org_id, user_id=user.id, role=inv.role))
        org = await s.get(Org, inv.org_id)
        admins = list(await s.scalars(select(Membership.user_id).where(
            Membership.org_id == inv.org_id, Membership.role.in_(("admin", "owner")),
            Membership.user_id != user.id)))  # fmt: skip
        if org is not None and admins:
            s.add(MemberEvent(org_id=org.id, region=org.region, user_id=user.id, user_name=user.name,
                              org_name=org.name, role=inv.role, recipients=admins))  # fmt: skip
    inv.accepted_at, inv.accepted_by = now, user.id
    if user.email_verified_at is None:
        user.email_verified_at = now
    user.last_active_org_id = inv.org_id
    await s.flush()


def _email(ctx: Ctx, org: Org, inviter: User, inv: Invite, raw: str) -> EmailMessage:
    return mail.invite_email(ctx.settings.web_url, inv.email, inviter.name, org.name, inv.role, raw, inv.message)


async def create(ctx: Ctx, actor_id: uuid.UUID, org_id: uuid.UUID, body: schemas.InvitesIn) -> list[schemas.Invite]:
    """One invite per email. Existing members are skipped; a pending invite for the same email is replaced."""
    outbox: list[EmailMessage] = []
    created: list[Invite] = []
    async with ctx.sm() as s, s.begin():
        await require_member(s, org_id, actor_id, "admin")
        org = await s.get(Org, org_id)
        inviter = await s.get(User, actor_id)
        assert org is not None and inviter is not None
        members = set(
            await s.scalars(
                select(User.email)
                .join(Membership, Membership.user_id == User.id)
                .where(Membership.org_id == org_id, User.email.in_(body.emails))
            )
        )
        now = utcnow()
        for email in body.emails:
            if email in members:
                continue
            await s.execute(
                update(Invite).where(Invite.org_id == org_id, Invite.email == email, *PENDING).values(revoked_at=now)
            )
            raw = new_token()
            inv = Invite(
                org_id=org_id,
                email=email,
                role=body.role,
                token_hash=hash_token(raw),
                invited_by=actor_id,
                message=body.message,
                expires_at=now + INVITE_TTL,
            )
            s.add(inv)
            created.append(inv)
            outbox.append(_email(ctx, org, inviter, inv, raw))
        await s.flush()
        result = [invite_out(i) for i in created]
    for msg in outbox:
        await mail.send_quietly(ctx.mailer, msg)
    return result


async def list_pending(ctx: Ctx, actor_id: uuid.UUID, org_id: uuid.UUID) -> list[schemas.Invite]:
    async with ctx.sm() as s:
        await require_member(s, org_id, actor_id, "admin")
        rows = await s.scalars(select(Invite).where(Invite.org_id == org_id, *PENDING).order_by(Invite.created_at))
        return [invite_out(i) for i in rows]


async def _pending_in_org(s: AsyncSession, org_id: uuid.UUID, invite_id: uuid.UUID) -> Invite:
    inv = await s.scalar(
        select(Invite).where(Invite.id == invite_id, Invite.org_id == org_id, *PENDING).with_for_update()
    )
    if inv is None:
        raise _not_found()
    return inv


async def resend(ctx: Ctx, actor_id: uuid.UUID, org_id: uuid.UUID, invite_id: uuid.UUID) -> None:
    """New token (the old link stops working) and a fresh 7-day expiry."""
    async with ctx.sm() as s, s.begin():
        await require_member(s, org_id, actor_id, "admin")
        inv = await _pending_in_org(s, org_id, invite_id)
        raw = new_token()
        inv.token_hash, inv.expires_at = hash_token(raw), utcnow() + INVITE_TTL
        org, inviter = await s.get(Org, org_id), await s.get(User, actor_id)
        assert org is not None and inviter is not None
        msg = _email(ctx, org, inviter, inv, raw)
    await mail.send_quietly(ctx.mailer, msg)


async def revoke(ctx: Ctx, actor_id: uuid.UUID, org_id: uuid.UUID, invite_id: uuid.UUID) -> None:
    async with ctx.sm() as s, s.begin():
        await require_member(s, org_id, actor_id, "admin")
        inv = await _pending_in_org(s, org_id, invite_id)
        inv.revoked_at = utcnow()


async def preview(ctx: Ctx, raw: str) -> schemas.InvitePreview:
    async with ctx.sm() as s:
        inv = await _by_token(s, raw)
        if inv is None or inv.revoked_at is not None:
            raise _not_found()
        org = await s.get(Org, inv.org_id)
        inviter = await s.get(User, inv.invited_by) if inv.invited_by else None
        assert org is not None
        return schemas.InvitePreview(
            org_name=org.name,
            inviter_name=inviter.name if inviter else None,
            email=inv.email,
            role=inv.role,  # type: ignore[arg-type]
            region=org.region,  # type: ignore[arg-type]
            expired=inv.accepted_at is not None or inv.expires_at <= utcnow(),
        )


async def accept(ctx: Ctx, user_id: uuid.UUID, raw: str, user_agent: str | None) -> Issued:
    async with ctx.sm() as s, s.begin():
        user = await s.get(User, user_id)
        if user is None:
            raise unauthenticated()
        inv = await load_acceptable(s, raw)
        if inv.email != user.email:
            raise email_mismatch()
        await accept_rows(s, inv, user)
        return await open_session(s, ctx, user, [inv.org_id], user_agent=user_agent)
