"""Access-token verification (RS256 via identity JWKS) → Principal. Claims: docs/contracts/identity-api.md."""

import uuid
from collections.abc import Mapping

import jwt
from pitchvision_schemas import ErrorCode

from pitchvision_api.auth.jwks import JwksClient, JwksUnavailable
from pitchvision_api.auth.principal import Principal, Role
from pitchvision_api.errors import ApiError
from pitchvision_api.settings import Settings

LEEWAY_S = 30
REQUIRED_CLAIMS = ["exp", "iat", "iss", "aud", "sub"]


def unauthenticated(detail: str) -> ApiError:
    return ApiError(
        ErrorCode.UNAUTHENTICATED, 401, "Authentication required", detail, headers={"WWW-Authenticate": "Bearer"}
    )


async def verify_access_token(token: str, settings: Settings, jwks: JwksClient) -> Principal:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        raise unauthenticated("Malformed access token.") from None
    if header.get("alg") != "RS256":
        raise unauthenticated("Unsupported token algorithm.")
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        raise unauthenticated("Access token has no key id.")
    try:
        key = await jwks.get_key(kid)
    except JwksUnavailable:
        raise ApiError(ErrorCode.INTERNAL, 503, "Cannot verify token", "Identity key set unavailable.") from None
    if key is None:
        raise unauthenticated("Access token signed with an unknown key.")
    try:
        claims = jwt.decode(
            token,
            key=key.key,
            algorithms=["RS256"],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            leeway=LEEWAY_S,
            options={"require": REQUIRED_CLAIMS},
        )
    except jwt.ExpiredSignatureError:
        raise unauthenticated("Access token expired; refresh it.") from None
    except jwt.PyJWTError as e:
        raise unauthenticated(f"Invalid access token ({type(e).__name__}).") from None
    return principal_from_claims(claims, settings)


def principal_from_claims(claims: Mapping[str, object], settings: Settings) -> Principal:
    try:
        user_id = uuid.UUID(str(claims["sub"]))
    except ValueError:
        raise unauthenticated("Invalid subject claim.") from None
    if claims.get("email_verified") is not True:
        raise ApiError(ErrorCode.EMAIL_NOT_VERIFIED, 403, "Email not verified", "Verify your email address first.")
    org, role = claims.get("org"), claims.get("role")
    if not org or not role:
        raise ApiError(ErrorCode.FORBIDDEN, 403, "No active organisation", "Create or switch to an organisation.")
    try:
        org_id, role_ = uuid.UUID(str(org)), Role(str(role))
    except ValueError:
        raise unauthenticated("Invalid org/role claim.") from None
    region = claims.get("region")
    if region != settings.region:
        raise ApiError(
            ErrorCode.REGION_MISMATCH,
            409,
            "Organisation lives in another region",
            f"This API serves region {settings.region!r}; the active organisation is in {region!r}.",
        )
    return Principal(user_id=user_id, org_id=org_id, role=role_, region=settings.region)
