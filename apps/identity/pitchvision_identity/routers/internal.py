"""/internal/v1 — service-to-service calls (not in the public OpenAPI). Bearer ``PV_INTERNAL_TOKEN``; without that
setting every route answers 404.

POST /internal/v1/notifications/email (M1-BE-19): a regional worker asks identity — which owns users' addresses
and the mailer — to email a SCR-51 notification. Not sent (``{"sent": false, reason}``) when the user is gone, no
longer a member of the org, or not verified, or the type has no email copy.

GET /internal/v1/erasures?region= + POST /internal/v1/erasures/{id}/done: deleted orgs / accounts whose data the
region still has to purge (services/erasure.py). GET /internal/v1/users/{id}/export: the account part of a data
export (SCR-40 D02).

GET /internal/v1/member-events?region= + POST /internal/v1/member-events/{id}/done: new memberships the region turns
into ``member_joined`` notifications for the org's admins (SCR-51).
"""

import hmac
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pitchvision_schemas import ErrorCode
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update

from pitchvision_identity.db.models import MemberEvent, Membership, User
from pitchvision_identity.deps import AppCtx
from pitchvision_identity.errors import ApiError
from pitchvision_identity.mail.templates import notification_email
from pitchvision_identity.services import erasure as erasure_svc

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


class ErasureOut(BaseModel):
    id: uuid.UUID
    kind: Literal["org", "user"]
    subject_id: uuid.UUID
    requested_at: datetime


class ErasureDoneIn(BaseModel):
    region: Literal["eu", "us"]


class ErasureDoneOut(BaseModel):
    completed: bool


@router.get("/erasures", dependencies=[Depends(_service)])
async def pending_erasures(ctx: AppCtx, region: Annotated[Literal["eu", "us"], Query()],
                           limit: Annotated[int, Query(ge=1, le=200)] = 50) -> list[ErasureOut]:  # fmt: skip
    rows = await erasure_svc.pending(ctx, region, limit)
    return [ErasureOut(id=r.id, kind=r.kind, subject_id=r.subject_id, requested_at=r.requested_at)  # type: ignore[arg-type]
            for r in rows]  # fmt: skip


@router.post("/erasures/{request_id}/done", dependencies=[Depends(_service)])
async def erasure_done(request_id: uuid.UUID, body: ErasureDoneIn, ctx: AppCtx) -> ErasureDoneOut:
    return ErasureDoneOut(completed=await erasure_svc.mark_done(ctx, request_id, body.region))


@router.get("/users/{user_id}/export", dependencies=[Depends(_service)])
async def user_export(user_id: uuid.UUID, ctx: AppCtx) -> dict[str, Any]:
    out = await erasure_svc.user_export(ctx, user_id)
    if out is None:
        raise ApiError(ErrorCode.NOT_FOUND, 404, "User not found")
    return out


class MemberEventOut(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    user_id: uuid.UUID
    user_name: str
    org_name: str
    role: str
    recipients: list[uuid.UUID]
    created_at: datetime


@router.get("/member-events", dependencies=[Depends(_service)])
async def pending_member_events(ctx: AppCtx, region: Annotated[Literal["eu", "us"], Query()],
                                limit: Annotated[int, Query(ge=1, le=200)] = 100) -> list[MemberEventOut]:  # fmt: skip
    async with ctx.sm() as s:
        rows = await s.scalars(select(MemberEvent).where(MemberEvent.region == region,
                                                         MemberEvent.delivered_at.is_(None))
                               .order_by(MemberEvent.created_at).limit(limit))  # fmt: skip
        return [MemberEventOut(id=e.id, org_id=e.org_id, user_id=e.user_id, user_name=e.user_name,
                               org_name=e.org_name, role=e.role, recipients=e.recipients,
                               created_at=e.created_at) for e in rows]  # fmt: skip


@router.post("/member-events/{event_id}/done", status_code=204, dependencies=[Depends(_service)])
async def member_event_done(event_id: uuid.UUID, ctx: AppCtx) -> None:
    async with ctx.sm() as s, s.begin():
        await s.execute(update(MemberEvent).where(MemberEvent.id == event_id, MemberEvent.delivered_at.is_(None))
                        .values(delivered_at=func.now()))  # fmt: skip
