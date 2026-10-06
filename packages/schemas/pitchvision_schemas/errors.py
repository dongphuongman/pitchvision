"""RFC 9457 problem details + stable error codes (AD §10.1, §16)."""
from enum import StrEnum

from pydantic import BaseModel

ERROR_TYPE_BASE = "https://docs.pitchvision.ai/errors/"


class ErrorCode(StrEnum):
    VALIDATION_FAILED = "VALIDATION_FAILED"
    NOT_FOUND = "NOT_FOUND"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    FORBIDDEN = "FORBIDDEN"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    CONFLICT = "CONFLICT"
    IDEMPOTENCY_KEY_REQUIRED = "IDEMPOTENCY_KEY_REQUIRED"
    IDEMPOTENCY_KEY_REUSED = "IDEMPOTENCY_KEY_REUSED"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    PLAN_REQUIRED = "PLAN_REQUIRED"  # 402: the feature / option needs a higher plan (UIUX F10 → SCR-41)
    RATE_LIMITED = "RATE_LIMITED"
    INTERNAL = "INTERNAL"
    # identity
    EMAIL_TAKEN = "EMAIL_TAKEN"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    EMAIL_NOT_VERIFIED = "EMAIL_NOT_VERIFIED"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    LAST_OWNER = "LAST_OWNER"
    INVITE_EMAIL_MISMATCH = "INVITE_EMAIL_MISMATCH"
    REGION_MISMATCH = "REGION_MISMATCH"
    # pipeline
    VIDEO_UNREADABLE = "VIDEO_UNREADABLE"
    NO_WIDE_SHOTS = "NO_WIDE_SHOTS"
    TEAM_SEPARATION_FAILED = "TEAM_SEPARATION_FAILED"
    HOMOGRAPHY_UNAVAILABLE = "HOMOGRAPHY_UNAVAILABLE"
    INGEST_FAILED = "INGEST_FAILED"
    EXPORT_FAILED = "EXPORT_FAILED"


class Problem(BaseModel):
    type: str
    title: str
    status: int
    code: ErrorCode
    detail: str | None = None
    instance: str | None = None

    @classmethod
    def of(
        cls, code: ErrorCode, status: int, title: str, detail: str | None = None, instance: str | None = None
    ) -> "Problem":
        slug = code.value.lower().replace("_", "-")
        return cls(type=ERROR_TYPE_BASE + slug, title=title, status=status, code=code, detail=detail, instance=instance)
