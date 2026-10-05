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
