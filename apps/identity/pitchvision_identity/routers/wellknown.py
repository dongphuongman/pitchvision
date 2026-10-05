"""GET /.well-known/jwks.json — public keys the regional API uses to verify access tokens."""

from fastapi import APIRouter, Response

from pitchvision_identity import schemas
from pitchvision_identity.deps import AppCtx

router = APIRouter(tags=["jwks"])


@router.get("/.well-known/jwks.json")
async def jwks(response: Response, ctx: AppCtx) -> schemas.Jwks:
    response.headers["Cache-Control"] = "public, max-age=300"
    return schemas.Jwks.model_validate(ctx.keys.jwks())
