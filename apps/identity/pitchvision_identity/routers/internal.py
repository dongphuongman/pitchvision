"""/internal/v1 — service-to-service calls (not in the public OpenAPI). Bearer ``PV_INTERNAL_TOKEN``; without that
setting every route answers 404.

POST /internal/v1/notifications/email (M1-BE-19): a regional worker asks identity — which owns users' addresses
and the mailer — to email a SCR-51 notification. Not sent (``{"sent": false, reason}``) when the user is gone, no
longer a member of the org, or not verified, or the type has no email copy.
"""

import hmac
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pitchvision_schemas import ErrorCode
from pydantic import BaseModel, Field
from sqlalchemy import select

from pitchvision_identity.db.models import Membership, User
from pitchvision_identity.deps import AppCtx
from pitchvision_identity.errors import ApiError
from pitchvision_identity.mail.templates import notification_email

router = APIRouter(prefix="/internal/v1", include_in_schema=False)
# notification type → users.email_prefs key (SCR-40 R03); types without a key (quota) always send
PREF_OF = {"job_ready": "job_ready", "needs_teams": "job_ready", "job_failed": "job_failed",
           "export_ready": "export_ready"}
_bearer = HTTPBearer(auto_error=False)


def _service(ctx: AppCtx, creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]) -> None:
    token = ctx.settings.internal_token
    if not token:
        raise ApiError(ErrorCode.NOT_FOUND, 404, "Not found")
    if creds is None or not hmac.compare_digest(creds.credentials.encode(), token.encode()):
        raise ApiError(ErrorCode.UNAUTHENTICATED, 401, "Service token required")


class NotificationEmailIn(BaseModel):
    user_id: uuid.UUID
    org_id: uuid.UUID
    type: str = Field(max_length=40)
    path: str = Field(pattern=r"^/[A-Za-z0-9/_\-]*$", max_length=200)
    video_title: str | None = Field(None, max_length=200)
    data: dict[str, Any] = Field(default_factory=dict)


class EmailOutcome(BaseModel):
    sent: bool
    reason: str | None = None


@router.post("/notifications/email", dependencies=[Depends(_service)])
async def notification_email_route(body: NotificationEmailIn, request: Request, ctx: AppCtx) -> EmailOutcome:
    async with ctx.sm() as s:
        user = await s.get(User, body.user_id)
        member = await s.scalar(select(Membership).where(Membership.user_id == body.user_id,
                                                         Membership.org_id == body.org_id))  # fmt: skip
    if user is None or member is None:
        return EmailOutcome(sent=False, reason="not_member")
    if not user.email_verified:
        return EmailOutcome(sent=False, reason="unverified")
    pref = PREF_OF.get(body.type)
    if pref is not None and (user.email_prefs or {}).get(pref) is False:
        return EmailOutcome(sent=False, reason="opted_out")
    msg = notification_email(ctx.settings.web_url, user.email, user.name, body.type, body.path, body.video_title,
                             body.data)  # fmt: skip
    if msg is None:
        return EmailOutcome(sent=False, reason="no_email_for_type")
    await ctx.mailer.send(msg)
    return EmailOutcome(sent=True)
