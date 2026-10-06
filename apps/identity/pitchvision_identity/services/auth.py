"""Email + password identity: register, login, verify, forgot/reset, unverified delete (M1-BE-05)."""

import uuid
from datetime import timedelta

from pitchvision_schemas import ErrorCode
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from pitchvision_identity import mail, schemas
from pitchvision_identity.context import Ctx
from pitchvision_identity.db.models import EmailToken, User
from pitchvision_identity.errors import ApiError
from pitchvision_identity.ratelimit import FORGOT, LOGIN_FAILURES, RESEND, enforce
from pitchvision_identity.security import hash_token, new_token, normalize_email, utcnow
from pitchvision_identity.services import invites as invite_svc
from pitchvision_identity.services.sessions import (
    Issued,
    membership_out,
    memberships_of,
    open_session,
    revoke_all_for_user,
    revoke_others,
    revoke_raw,
    unauthenticated,
)

VERIFY_TTL = timedelta(hours=24)
RESET_TTL = timedelta(minutes=30)
VERIFY, RESET = "verify_email", "reset_password"


def token_expired() -> ApiError:
    return ApiError(ErrorCode.TOKEN_EXPIRED, 410, "Link expired or already used", "Request a new link.")


async def _new_email_token(s: AsyncSession, user_id: uuid.UUID, purpose: str, ttl: timedelta) -> str:
    """Issue a single-use email token; older unused tokens of the same purpose stop working."""
    now = utcnow()
    await s.execute(
        update(EmailToken)
        .where(EmailToken.user_id == user_id, EmailToken.purpose == purpose, EmailToken.used_at.is_(None))
        .values(used_at=now)
    )
    raw = new_token()
    s.add(EmailToken(user_id=user_id, purpose=purpose, token_hash=hash_token(raw), expires_at=now + ttl))
    return raw


async def _consume_email_token(s: AsyncSession, raw: str, purpose: str) -> EmailToken:
    row = await s.scalar(
        select(EmailToken)
        .where(EmailToken.token_hash == hash_token(raw), EmailToken.purpose == purpose)
        .with_for_update()
    )
    if row is None or row.used_at is not None or row.expires_at <= utcnow():
        raise token_expired()
    row.used_at = utcnow()
    return row


async def register(ctx: Ctx, body: schemas.RegisterIn, user_agent: str | None) -> Issued:
    email = normalize_email(body.email)
    password_hash = await ctx.hasher.hash(body.password)
    verify_raw: str | None = None
    async with ctx.sm() as s, s.begin():
        if await s.scalar(select(User.id).where(User.email == email)):
            raise _email_taken()
        invite = None
        if body.invite_token:
            invite = await invite_svc.load_acceptable(s, body.invite_token)
            if invite.email != email:
                raise invite_svc.email_mismatch()
        user = User(
            name=body.name,
            email=email,
            password_hash=password_hash,
            signup_plan=body.plan,
            email_verified_at=utcnow() if invite else None,  # the invite link proved the address
        )
        s.add(user)
        try:
            await s.flush()
        except IntegrityError:
            raise _email_taken() from None
        if invite is not None:
            await invite_svc.accept_rows(s, invite, user)
        else:
            verify_raw = await _new_email_token(s, user.id, VERIFY, VERIFY_TTL)
        issued = await open_session(s, ctx, user, [invite.org_id if invite else None], user_agent=user_agent)
    if verify_raw:
        await mail.send_quietly(ctx.mailer, mail.verify_email(ctx.settings.web_url, email, user.name, verify_raw))
    return issued


def _email_taken() -> ApiError:
    return ApiError(ErrorCode.EMAIL_TAKEN, 409, "Email already registered", "Sign in instead.")


async def login(ctx: Ctx, body: schemas.LoginIn, ip: str, current_raw: str | None, user_agent: str | None) -> Issued:
    email = normalize_email(body.email)
    key = f"{ip}|{email}"
    await enforce(ctx.limiter, LOGIN_FAILURES, key, count=False)
    async with ctx.sm() as s, s.begin():
        user = await s.scalar(select(User).where(User.email == email))
        if not await ctx.hasher.verify(user.password_hash if user else None, body.password) or user is None:
            await ctx.limiter.hit(LOGIN_FAILURES, key)
            raise ApiError(ErrorCode.INVALID_CREDENTIALS, 401, "Invalid email or password")
        await ctx.limiter.reset(LOGIN_FAILURES, key)
        if user.password_hash and ctx.hasher.needs_rehash(user.password_hash):
            user.password_hash = await ctx.hasher.hash(body.password)
        await revoke_raw(s, current_raw, user.id)  # signing in again replaces this browser's old session
        return await open_session(s, ctx, user, user_agent=user_agent)


async def verify_email(ctx: Ctx, token: str, current_raw: str | None, user_agent: str | None) -> Issued:
    async with ctx.sm() as s, s.begin():
        row = await _consume_email_token(s, token, VERIFY)
        user = await s.get(User, row.user_id)
        if user is None:
            raise token_expired()
        if user.email_verified_at is None:
            user.email_verified_at = utcnow()
        await revoke_raw(s, current_raw, user.id)
        return await open_session(s, ctx, user, user_agent=user_agent)


async def resend_verification(ctx: Ctx, user_id: uuid.UUID, ip: str) -> None:
    async with ctx.sm() as s, s.begin():
        user = await s.get(User, user_id)
        if user is None or user.email_verified:
            return
        await enforce(ctx.limiter, RESEND, f"{ip}|{user.email}")
        raw = await _new_email_token(s, user.id, VERIFY, VERIFY_TTL)
    await mail.send_quietly(ctx.mailer, mail.verify_email(ctx.settings.web_url, user.email, user.name, raw))


async def forgot_password(ctx: Ctx, body: schemas.ForgotIn, ip: str) -> None:
    """Always 202: the response never says whether the account exists."""
    email = normalize_email(body.email)
    await enforce(ctx.limiter, FORGOT, f"{ip}|{email}")
    async with ctx.sm() as s, s.begin():
        user = await s.scalar(select(User).where(User.email == email))
        if user is None:
            return
        raw = await _new_email_token(s, user.id, RESET, RESET_TTL)
    await mail.send_quietly(ctx.mailer, mail.reset_email(ctx.settings.web_url, user.email, user.name, raw))


async def reset_password(ctx: Ctx, body: schemas.ResetIn) -> None:
    password_hash = await ctx.hasher.hash(body.password)
    async with ctx.sm() as s, s.begin():
        row = await _consume_email_token(s, body.token, RESET)
        user = await s.get(User, row.user_id)
        if user is None:
            raise token_expired()
        user.password_hash = password_hash
        user.updated_at = utcnow()
        if user.email_verified_at is None:  # the reset link proved the address
            user.email_verified_at = utcnow()
        await revoke_all_for_user(s, user.id)  # UIUX SCR-05: sign out every session


async def delete_unverified(ctx: Ctx, user_id: uuid.UUID) -> None:
    """SCR-06 "Change it": drop an account that never verified its email (cascade: tokens, memberships)."""
    async with ctx.sm() as s, s.begin():
        user = await s.get(User, user_id, with_for_update=True)
        if user is None:
            return
        if user.email_verified:
            raise ApiError(
                ErrorCode.CONFLICT, 409, "Email already verified", "Only unverified accounts can be deleted here."
            )
        await s.delete(user)


async def me(ctx: Ctx, user_id: uuid.UUID) -> schemas.Me:
    async with ctx.sm() as s:
        user = await s.get(User, user_id)
        if user is None:
            raise unauthenticated()
        ms = await memberships_of(s, user.id)
    return schemas.Me(
        id=user.id,
        name=user.name,
        email=user.email,
        email_verified=user.email_verified,
        created_at=user.created_at,
        memberships=[membership_out(m, o) for m, o in ms],
        email_prefs=email_prefs(user),
    )


def email_prefs(user: User) -> dict[schemas.EmailPrefKey, bool]:
    stored = user.email_prefs or {}
    return {k: bool(stored.get(k, True)) for k in schemas.EMAIL_PREF_KEYS}  # type: ignore[misc]


async def patch_me(ctx: Ctx, user_id: uuid.UUID, body: schemas.MePatchIn) -> schemas.Me:
    async with ctx.sm() as s, s.begin():
        user = await s.get(User, user_id, with_for_update=True)
        if user is None:
            raise unauthenticated()
        if body.name is not None:
            user.name = body.name
        if body.email_prefs:
            user.email_prefs = {**(user.email_prefs or {}), **{str(k): v for k, v in body.email_prefs.items()}}
        user.updated_at = utcnow()
    return await me(ctx, user_id)


async def change_password(ctx: Ctx, user_id: uuid.UUID, body: schemas.PasswordChangeIn, current_raw: str | None
                          ) -> None:  # fmt: skip
    """SCR-40 P03: needs the current password (Google-only accounts set one via "forgot password"); every other
    session is signed out, this one stays."""
    async with ctx.sm() as s:
        user = await s.get(User, user_id)
    if user is None:
        raise unauthenticated()
    if not user.password_hash or not await ctx.hasher.verify(user.password_hash, body.current_password):
        raise ApiError(ErrorCode.INVALID_CREDENTIALS, 401, "Current password is wrong")
    new_hash = await ctx.hasher.hash(body.new_password)
    async with ctx.sm() as s, s.begin():
        row = await s.get(User, user_id, with_for_update=True)
        if row is None:
            raise unauthenticated()
        row.password_hash, row.updated_at = new_hash, utcnow()
        await revoke_others(s, user_id, current_raw)


async def sign_out_others(ctx: Ctx, user_id: uuid.UUID, current_raw: str | None) -> None:
    """SCR-40 P04."""
    async with ctx.sm() as s, s.begin():
        await revoke_others(s, user_id, current_raw)
