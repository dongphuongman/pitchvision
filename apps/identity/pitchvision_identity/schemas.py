"""Request/response shapes of docs/contracts/identity-api.md (OpenAPI = apps/identity/openapi.json)."""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, field_validator

Role = Literal["owner", "admin", "editor", "viewer"]
InviteRole = Literal["admin", "editor", "viewer"]
Region = Literal["eu", "us"]
OrgKind = Literal["club", "academy", "media", "agency", "individual", "other"]

ROLE_RANK: dict[str, int] = {"viewer": 0, "editor": 1, "admin": 2, "owner": 3}

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Password = Annotated[str, Field(min_length=10, max_length=256)]
Token = Annotated[str, Field(min_length=10, max_length=256)]

MAX_INVITES_PER_REQUEST = 50


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------ requests


class RegisterIn(_In):
    name: Name
    email: EmailStr
    password: Password
    invite_token: Token | None = None
    plan: Annotated[str, Field(max_length=32)] | None = None


class LoginIn(_In):
    email: EmailStr
    password: Annotated[str, Field(min_length=1, max_length=256)]


class RefreshIn(_In):
    org_id: uuid.UUID | None = None


class TokenIn(_In):
    token: Token


class ForgotIn(_In):
    email: EmailStr


class ResetIn(_In):
    token: Token
    password: Password


class OrgCreateIn(_In):
    name: Name
    kind: OrgKind
    region: Region
    my_role_title: Annotated[str, StringConstraints(strip_whitespace=True, max_length=60)] | None = None


class OrgPatchIn(_In):
    """Region is not accepted: it is chosen once at creation (sending it is a 422)."""

    name: Name | None = None


class OrgDeleteIn(_In):
    """DLG-08: the org's name typed again (compared trimmed, case-sensitive)."""

    confirm_name: str = Field(max_length=200)


class MeDeleteIn(_In):
    """DLG-08: the account's email typed again (compared trimmed, case-insensitive)."""

    confirm_email: str = Field(max_length=320)


class OrgRef(BaseModel):
    id: uuid.UUID
    name: str
    region: Region


class DeletionPreview(BaseModel):
    """What DELETE /me would do: ``blocking`` orgs need another owner first (you are their only owner and they have
    other members); ``deleted_with_account`` orgs have no other member and are deleted with the account."""

    blocking: list[OrgRef]
    deleted_with_account: list[OrgRef]


EmailPrefKey = Literal["job_ready", "job_failed", "export_ready"]
EMAIL_PREF_KEYS: tuple[str, ...] = ("job_ready", "job_failed", "export_ready")


class AnalysisDefaults(_In):
    """SCR-12 E10 "Save as my default" / SCR-40 R02 (M1 scope: mode + detection confidence)."""

    mode: Literal["standard", "fast"] = "standard"
    confidence: float = Field(0.35, ge=0.2, le=0.8)


class MePatchIn(_In):
    """SCR-40 Profile P01 (name), Preferences R03 (email notifications; omitted keys unchanged) and R02 (analysis
    defaults; ``null`` = back to the product defaults)."""

    name: Name | None = None
    email_prefs: dict[EmailPrefKey, bool] | None = None
    analysis_defaults: AnalysisDefaults | None = None


class PasswordChangeIn(_In):
    current_password: Annotated[str, Field(min_length=1, max_length=256)]
    new_password: Password


UpgradePlan = Literal["pro", "team"]
UpgradeReason = Literal["quota", "feature", "seats", "quality"]


class UpgradeRequestIn(_In):
    plan: UpgradePlan = "pro"
    period: Literal["monthly", "annual"] = "monthly"
    reason: UpgradeReason | None = None


class UpgradeRequestOut(BaseModel):
    id: uuid.UUID
    plan: UpgradePlan
    period: str
    owners_notified: int = Field(description="owners emailed (when the requester is not an owner)")
    created_at: datetime


class MemberPatchIn(_In):
    role: Role


class InvitesIn(_In):
    emails: Annotated[list[EmailStr], Field(min_length=1, max_length=MAX_INVITES_PER_REQUEST)]
    role: InviteRole
    message: Annotated[str, Field(max_length=500)] | None = None

    @field_validator("emails")
    @classmethod
    def _dedupe(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(e.strip().lower() for e in v))


# ------------------------------------------------------------------ responses


class User(BaseModel):
    id: uuid.UUID
    name: str
    email: str
    email_verified: bool
    created_at: datetime


class Membership(BaseModel):
    org_id: uuid.UUID
    org_name: str
    role: Role
    region: Region


class Me(User):
    memberships: list[Membership]
    email_prefs: dict[EmailPrefKey, bool] = Field(
        default_factory=dict, description="job_ready / job_failed / export_ready — every key present (default on)")
    analysis_defaults: AnalysisDefaults | None = Field(None, description="SCR-12 defaults; null = product defaults")


class Session(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user: User
    memberships: list[Membership]
    active_org_id: uuid.UUID | None


class Org(BaseModel):
    id: uuid.UUID
    name: str
    kind: OrgKind
    region: Region
    created_at: datetime


class Member(BaseModel):
    user_id: uuid.UUID
    name: str
    email: str
    role: Role
    joined_at: datetime


class Invite(BaseModel):
    id: uuid.UUID
    email: str
    role: InviteRole
    expires_at: datetime
    created_at: datetime
    invited_by: uuid.UUID | None


class InvitePreview(BaseModel):
    org_name: str
    inviter_name: str | None
    email: str
    role: InviteRole
    region: Region
    expired: bool


class Jwk(BaseModel):
    model_config = ConfigDict(extra="allow")
    kty: str
    kid: str
    use: str
    alg: str
    n: str
    e: str


class Jwks(BaseModel):
    keys: list[Jwk]
