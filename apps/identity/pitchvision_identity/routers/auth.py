"""/api/v1/auth/*: email+password, refresh rotation, email verification, password reset, Google OIDC."""

import logging
from typing import Annotated

import httpx
from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from pitchvision_schemas import ErrorCode

from pitchvision_identity import schemas
from pitchvision_identity.deps import (
    OIDC_COOKIE,
    OIDC_COOKIE_PATH,
    AppCtx,
    UserId,
    clear_refresh_cookie,
    client_ip,
    refresh_cookie,
    set_refresh_cookie,
    user_agent,
)
from pitchvision_identity.errors import ApiError
from pitchvision_identity.services import auth as svc
from pitchvision_identity.services import oidc
from pitchvision_identity.services import sessions as sessions_svc
from pitchvision_identity.services.sessions import Issued

router = APIRouter(prefix="/auth", tags=["auth"])
log = logging.getLogger(__name__)


def _session(response: Response, ctx: AppCtx, issued: Issued) -> schemas.Session:
    set_refresh_cookie(response, ctx, issued.refresh_token)
    response.headers["Cache-Control"] = "no-store"
    return issued.session


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(body: schemas.RegisterIn, request: Request, response: Response, ctx: AppCtx) -> schemas.Session:
    """Create an account. With a matching `invite_token` the email counts as verified and the membership is
    created; otherwise a verification email is sent."""
    return _session(response, ctx, await svc.register(ctx, body, user_agent(request)))


@router.post("/login")
async def login(body: schemas.LoginIn, request: Request, response: Response, ctx: AppCtx) -> schemas.Session:
    issued = await svc.login(ctx, body, client_ip(request), refresh_cookie(request), user_agent(request))
    return _session(response, ctx, issued)


@router.post("/refresh")
async def refresh(
    request: Request, response: Response, ctx: AppCtx, body: schemas.RefreshIn | None = None
) -> schemas.Session:
    """Rotate the `pv_refresh` cookie. `{org_id}` switches the active org (must be a member, else 403)."""
    try:
        issued = await sessions_svc.rotate(
            ctx, refresh_cookie(request), body.org_id if body else None, user_agent(request)
        )
    except ApiError as e:
        if e.status == 401:  # dead cookie: tell the browser to drop it
            e.headers = {**(e.headers or {}), "Set-Cookie": _expired_cookie_header(ctx)}
        raise
    return _session(response, ctx, issued)


def _expired_cookie_header(ctx: AppCtx) -> str:
    r = Response()
    clear_refresh_cookie(r, ctx)
    return r.headers["set-cookie"]


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, ctx: AppCtx) -> Response:
    await sessions_svc.logout(ctx, refresh_cookie(request))
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_refresh_cookie(response, ctx)
    return response


@router.post("/verify-email")
async def verify_email(body: schemas.TokenIn, request: Request, response: Response, ctx: AppCtx) -> schemas.Session:
    """Single-use, 24 h. Expired/used/unknown -> 410 TOKEN_EXPIRED."""
    issued = await svc.verify_email(ctx, body.token, refresh_cookie(request), user_agent(request))
    return _session(response, ctx, issued)


@router.post("/verify-email/resend", status_code=status.HTTP_202_ACCEPTED)
async def resend_verification(request: Request, user_id: UserId, ctx: AppCtx) -> Response:
    await svc.resend_verification(ctx, user_id, client_ip(request))
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.post("/password/forgot", status_code=status.HTTP_202_ACCEPTED)
async def forgot_password(body: schemas.ForgotIn, request: Request, ctx: AppCtx) -> Response:
    """Always 202, whether or not the account exists. The link is valid 30 min."""
    await svc.forgot_password(ctx, body, client_ip(request))
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.post("/password/reset", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(body: schemas.ResetIn, ctx: AppCtx) -> Response:
    """Sets the password and revokes every refresh token of the user. Expired/used -> 410 TOKEN_EXPIRED."""
    await svc.reset_password(ctx, body)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------ Google OIDC


def _google(ctx: AppCtx) -> oidc.GoogleOidc:
    if ctx.google is None:
        raise ApiError(ErrorCode.NOT_FOUND, 404, "Google sign-in is not configured")
    return ctx.google


@router.get("/oidc/google/start", status_code=status.HTTP_302_FOUND, response_class=RedirectResponse)
async def google_start(ctx: AppCtx, return_to: Annotated[str | None, Query(max_length=512)] = None) -> RedirectResponse:
    """302 to Google with state + nonce + PKCE (S256); the pending request rides in an HttpOnly cookie."""
    url, cookie = await _google(ctx).start(return_to)
    resp = RedirectResponse(url, status_code=status.HTTP_302_FOUND)
    resp.set_cookie(
        OIDC_COOKIE,
        cookie,
        max_age=oidc.STATE_TTL_S,
        path=OIDC_COOKIE_PATH,
        secure=ctx.settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return resp


@router.get("/oidc/google/callback", status_code=status.HTTP_302_FOUND, response_class=RedirectResponse)
async def google_callback(
    request: Request,
    ctx: AppCtx,
    code: Annotated[str | None, Query(max_length=2048)] = None,
    state: Annotated[str | None, Query(max_length=512)] = None,
    error: Annotated[str | None, Query(max_length=256)] = None,
) -> RedirectResponse:
    """302 to `{web}/auth/callback#status=ok&new_user=…&return_to=…` with the refresh cookie set, or
    `#error=<ErrorCode>`. The web then calls POST /auth/refresh to get the Session."""
    web = ctx.settings.web_url
    google = _google(ctx)
    try:
        if error:
            raise oidc.OidcError("UNAUTHENTICATED", f"google returned error={error}")
        pending = google.pending_from_cookie(request.cookies.get(OIDC_COOKIE), state)
        if not code:
            raise oidc.OidcError("UNAUTHENTICATED", "missing code")
        identity = await google.exchange(code, pending)
        issued, created = await oidc.sign_in(ctx, identity, user_agent(request))
    except (oidc.OidcError, httpx.HTTPError) as e:
        log.info("google sign-in failed: %r", e)
        code = e.code if isinstance(e, oidc.OidcError) else "UNAUTHENTICATED"
        resp = RedirectResponse(oidc.callback_url(web, error=code), status_code=status.HTTP_302_FOUND)
    else:
        fragment = {"status": "ok", "new_user": "true" if created else "false"}
        if pending.return_to:
            fragment["return_to"] = pending.return_to
        resp = RedirectResponse(oidc.callback_url(web, **fragment), status_code=status.HTTP_302_FOUND)
        set_refresh_cookie(resp, ctx, issued.refresh_token)
    resp.delete_cookie(OIDC_COOKIE, path=OIDC_COOKIE_PATH, secure=ctx.settings.cookie_secure, httponly=True)
    return resp
