"""Idempotency-Key for unsafe POSTs (AD §10.1: required on POST /jobs and POST /exports).

Usage — the handler only runs for a new key; replays never reach it:

    @router.post("/jobs", status_code=202)
    async def create_job(body: JobIn, idem: Idem, session: DbSession) -> JSONResponse:
        job = await jobs.create(session, body)
        return await idem.respond(JobOut.model_validate(job), status_code=202)

- missing header                → 400 IDEMPOTENCY_KEY_REQUIRED
- same key, same request        → stored status + body replayed (`Idempotent-Replayed: true`)
- same key, different request   → 422 IDEMPOTENCY_KEY_REUSED
The claim shares the request's transaction, so an endpoint error rolls it back and the key can be retried;
only successful responses are stored. Keys are per org and expire after 24 h.
"""
import hashlib
import json
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pitchvision_schemas import ErrorCode
from sqlalchemy.ext.asyncio import AsyncSession

from pitchvision_api.deps import CurrentPrincipal, DbSession
from pitchvision_api.errors import ApiError
from pitchvision_api.repositories import idempotency as repo

IDEMPOTENCY_HEADER = "Idempotency-Key"
REPLAYED_HEADER = "Idempotent-Replayed"


class IdempotentReplay(Exception):
    def __init__(self, key: str, status: int, body: Any) -> None:
        super().__init__(key)
        self.key, self.status, self.body = key, status, body


class Idempotency:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID, key: str) -> None:
        self._session, self._org_id, self.key = session, org_id, key
        self.responded = False

    async def respond(self, content: Any, status_code: int = 200) -> JSONResponse:
        """Store the response under the key (same transaction as the handler's writes) and return it."""
        body = jsonable_encoder(content)
        await repo.save_response(self._session, self._org_id, self.key, status_code, body)
        self.responded = True
        return JSONResponse(body, status_code=status_code, headers={IDEMPOTENCY_HEADER: self.key})


def request_fingerprint(method: str, path: str, query: str, body: bytes) -> str:
    """sha256 over method, path+query and the body (canonical JSON when it parses, so key order doesn't matter)."""
    try:
        canonical = json.dumps(json.loads(body), sort_keys=True, separators=(",", ":")).encode() if body else b""
    except ValueError:
        canonical = body
    h = hashlib.sha256(f"{method}\n{path}?{query}\n".encode())
    h.update(canonical)
    return h.hexdigest()


async def _idempotency(
    request: Request,
    principal: CurrentPrincipal,
    session: DbSession,
    key: Annotated[
        str | None,
        Header(alias=IDEMPOTENCY_HEADER, min_length=1, max_length=255, description="Required. Unique per request."),
    ] = None,
) -> AsyncIterator[Idempotency]:
    if key is None:
        raise ApiError(
            ErrorCode.IDEMPOTENCY_KEY_REQUIRED,
            400,
            "Idempotency-Key header required",
            f"{request.method} {request.url.path} must carry an {IDEMPOTENCY_HEADER} header.",
        )
    fingerprint = request_fingerprint(request.method, request.url.path, request.url.query, await request.body())
    existing = await repo.claim(session, principal.org_id, key, request.method, request.url.path, fingerprint)
    if existing is None:
        guard = Idempotency(session, principal.org_id, key)
        yield guard
        if not guard.responded:  # handler returned without respond(): don't pin the key to nothing
            await repo.release(session, principal.org_id, key)
        return
    if existing.request_hash != fingerprint:
        raise ApiError(
            ErrorCode.IDEMPOTENCY_KEY_REUSED,
            422,
            "Idempotency-Key reused with a different request",
            "Use a new key for a different request body or endpoint.",
        )
    if existing.response_status is None:
        raise ApiError(ErrorCode.CONFLICT, 409, "Request with this Idempotency-Key is still in progress")
    raise IdempotentReplay(key, existing.response_status, existing.response_body)


Idem = Annotated[Idempotency, Depends(_idempotency, scope="function")]


async def _replay(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, IdempotentReplay)
    headers = {IDEMPOTENCY_HEADER: exc.key, REPLAYED_HEADER: "true"}
    return JSONResponse(exc.body, status_code=exc.status, headers=headers)


def install_idempotency(app: FastAPI) -> None:
    app.add_exception_handler(IdempotentReplay, _replay)
