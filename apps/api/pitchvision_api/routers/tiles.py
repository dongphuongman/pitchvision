"""GET /api/v1/tiles/{video_id}/{job_id}/r{revision}/{tile_no}.json.gz?exp=&sig= — self-host tile server (M1-BE-16).

AD §9.5: tiles are served by a Cloudflare Worker in the cloud and by this route when self-hosting. No bearer token:
the HMAC signature issued with the manifest (services.tile_signing) is the credential, so a plain ``fetch`` / CDN can
use it. Only AFTER the signature check the object is streamed from storage with ``Content-Encoding: gzip`` (the
stored bytes are gzip JSON; browsers decode them transparently) and ``Cache-Control: public, max-age=31536000,
immutable`` — tile keys are write-once per revision. Errors are ``no-store``.
"""

import asyncio
import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Path, Query, Request
from fastapi.responses import StreamingResponse
from pitchvision_schemas import ErrorCode, keys

from pitchvision_api.errors import ApiError
from pitchvision_api.repositories import results as repo
from pitchvision_api.services.storage import STORAGE_ERRORS, Storage, storage_error
from pitchvision_api.services.tile_signing import tile_scope, verify

router = APIRouter(tags=["results"])

IMMUTABLE = "public, max-age=31536000, immutable"
NO_STORE = {"Cache-Control": "no-store"}
CHUNK = 64 * 1024
ORG_CACHE_SIZE = 10_000


def _forbidden() -> ApiError:
    return ApiError(ErrorCode.FORBIDDEN, 403, "Invalid or expired tile signature",
                    "Fetch the result manifest again for fresh tile URLs.", headers=NO_STORE)  # fmt: skip


def _missing() -> ApiError:
    return ApiError(ErrorCode.NOT_FOUND, 404, "Tile not found", headers=NO_STORE)


async def _job_org(request: Request, job_id: uuid.UUID) -> uuid.UUID | None:
    """job → org (immutable), cached per process; the lookup is a SECURITY DEFINER helper returning ids only."""
    cache: OrderedDict[uuid.UUID, uuid.UUID] | None = getattr(request.app.state, "tile_orgs", None)
    if cache is None:
        cache = request.app.state.tile_orgs = OrderedDict()
    if job_id in cache:
        cache.move_to_end(job_id)
        return cache[job_id]
    async with request.app.state.sessionmaker() as session:
        org = await repo.job_org(session, job_id)
    if org is not None:
        cache[job_id] = org
        while len(cache) > ORG_CACHE_SIZE:
            cache.popitem(last=False)
    return org


async def _chunks(body: Any) -> AsyncIterator[bytes]:
    try:
        while chunk := await asyncio.to_thread(body.read, CHUNK):
            yield chunk
    finally:
        body.close()


TILE_RESPONSE = {"description": "gzip-encoded `Tile` JSON (Content-Encoding: gzip)",
                 "content": {"application/json": {"schema": {"type": "object"}}}}  # fmt: skip


@router.get(
    "/tiles/{video_id}/{job_id}/r{revision}/{tile_no}.json.gz",
    response_class=StreamingResponse,
    responses={200: TILE_RESPONSE},
)
async def get_tile(
    video_id: uuid.UUID,
    job_id: uuid.UUID,
    revision: Annotated[int, Path(ge=0)],
    tile_no: Annotated[int, Path(ge=0, le=999_999)],
    request: Request,
    exp: Annotated[int, Query(description="unix seconds; from the manifest's tile_url_template")],
    sig: Annotated[str, Query(max_length=128)],
) -> StreamingResponse:
    """One playback tile (`pitchvision_schemas.Tile`). Use the manifest's `tile_url_template`; 403 when the
    signature is invalid or expired."""
    settings = request.app.state.settings
    if not verify(settings.tile_key, tile_scope(video_id, job_id, revision), exp, sig, time.time()):
        raise _forbidden()
    org = await _job_org(request, job_id)
    if org is None:
        raise _missing()
    storage: Storage = request.app.state.storage
    key = keys.tile_key(str(org), str(video_id), str(job_id), revision, tile_no)
    try:
        obj = await asyncio.to_thread(storage.open_object, key)
    except STORAGE_ERRORS as e:
        raise storage_error(e) from e
    if obj is None:
        raise _missing()
    headers = {"Content-Encoding": "gzip", "Cache-Control": IMMUTABLE, "Content-Length": str(obj.size)}
    if obj.etag:
        headers["ETag"] = obj.etag
    return StreamingResponse(_chunks(obj.body), media_type="application/json", headers=headers)
