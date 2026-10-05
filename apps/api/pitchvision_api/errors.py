"""RFC 9457 problem+json for every error the API returns (AD §10.1)."""
import logging
from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pitchvision_schemas import ErrorCode, Problem
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from pitchvision_api.middleware import REQUEST_ID_HEADER, request_id_of

PROBLEM_JSON = "application/problem+json"

log = logging.getLogger(__name__)

_CODE_BY_STATUS = {
    400: ErrorCode.VALIDATION_FAILED,
    401: ErrorCode.UNAUTHENTICATED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.NOT_FOUND,  # no METHOD_NOT_ALLOWED in the shared ErrorCode set
    409: ErrorCode.CONFLICT,
    422: ErrorCode.VALIDATION_FAILED,
    429: ErrorCode.RATE_LIMITED,
}


class FieldError(BaseModel):
    loc: list[str | int]
    msg: str
    type: str


class ProblemDetails(Problem):
    """OpenAPI shape of every 4xx/5xx body: Problem + optional `errors` extension (422 only)."""

    errors: list[FieldError] | None = None


class ApiError(Exception):
    """Raise from any layer; rendered as problem+json with a stable ErrorCode."""

    def __init__(
        self, code: ErrorCode, status: int, title: str, detail: str | None = None, headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(title)
        self.code, self.status, self.title, self.detail, self.headers = code, status, title, detail, headers


def problem_response(
    request: Request,
    code: ErrorCode,
    status: int,
    title: str,
    detail: str | None = None,
    extensions: dict[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body = Problem.of(code, status, title, detail, instance=request.url.path).model_dump(mode="json", exclude_none=True)
    return JSONResponse(body | (extensions or {}), status_code=status, media_type=PROBLEM_JSON, headers=headers)


async def _api_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return problem_response(request, exc.code, exc.status, exc.title, exc.detail, headers=exc.headers)


async def _http_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    status = exc.status_code
    title = HTTPStatus(status).phrase
    code = _CODE_BY_STATUS.get(status, ErrorCode.INTERNAL if status >= 500 else ErrorCode.VALIDATION_FAILED)
    detail = exc.detail if isinstance(exc.detail, str) and exc.detail != title else None
    return problem_response(request, code, status, title, detail, headers=exc.headers)


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    errors = [FieldError(loc=list(e["loc"]), msg=e["msg"], type=e["type"]).model_dump() for e in exc.errors()]
    return problem_response(
        request,
        ErrorCode.VALIDATION_FAILED,
        422,
        "Request validation failed",
        f"{len(errors)} invalid field(s)",
        extensions={"errors": errors},
    )


async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    """Last resort: log with the request id, never leak the exception to the client."""
    rid = request_id_of(request)
    log.exception("unhandled error request_id=%s path=%s", rid, request.url.path, exc_info=exc)
    return problem_response(
        request,
        ErrorCode.INTERNAL,
        500,
        "Internal Server Error",
        "Unexpected error; quote the request id when reporting it.",
        extensions={"request_id": rid} if rid else None,
        headers={REQUEST_ID_HEADER: rid} if rid else None,  # ServerErrorMiddleware sits outside RequestIdMiddleware
    )


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(Exception, _unhandled)
