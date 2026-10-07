"""App factory. `uvicorn pitchvision_identity.main:app` (module-level app reads PV_* env).

Served on the web origin next to the regional api (nginx path ownership in docs/contracts/identity-api.md),
so its own OpenAPI/docs live under /api/v1/auth/ instead of the api's /api/v1/openapi.json.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

from pitchvision_identity import __version__
from pitchvision_identity.context import Ctx
from pitchvision_identity.db import make_engine, make_sessionmaker
from pitchvision_identity.errors import PROBLEM_JSON, ProblemDetails, install_error_handlers
from pitchvision_identity.mail import EmailSender, make_sender
from pitchvision_identity.middleware import REQUEST_ID_HEADER, RequestIdMiddleware
from pitchvision_identity.ratelimit import MemoryRateLimiter, RateLimiter
from pitchvision_identity.routers import auth, health, internal, invites, me, orgs, wellknown
from pitchvision_identity.security import KeyRing, PasswordHasher
from pitchvision_identity.services.oidc import GoogleOidc
from pitchvision_identity.settings import Settings

API_PREFIX = "/api/v1"
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {"4XX": {"model": ProblemDetails}, "5XX": {"model": ProblemDetails}}


def api_router() -> APIRouter:
    router = APIRouter(prefix=API_PREFIX, responses=ERROR_RESPONSES)
    for r in (auth.router, me.router, orgs.router, invites.router):
        router.include_router(r)
    return router


def _init_sentry(settings: Settings) -> None:
    """Errors to Sentry when PV_SENTRY_DSN is set (AD §17). No PII: request bodies / emails are never sent."""
    if not settings.sentry_dsn:
        return
    import sentry_sdk

    sentry_sdk.init(dsn=settings.sentry_dsn, environment=settings.env, release=settings.image_tag,
                    send_default_pii=False)  # fmt: skip
    sentry_sdk.set_tag("service", "identity")


def create_app(
    settings: Settings | None = None,
    *,
    http: httpx.AsyncClient | None = None,
    mailer: EmailSender | None = None,
    limiter: RateLimiter | None = None,
) -> FastAPI:
    """Tests inject `http` (fake Google), `mailer` (outbox) and `limiter`; production builds them from settings."""
    settings = settings or Settings()
    _init_sentry(settings)
    engine = make_engine(settings.identity_database_url)  # lazy: no connection until first use
    own_http = http is None
    http = http or httpx.AsyncClient(follow_redirects=False)
    keys = KeyRing(settings)
    ctx = Ctx(
        settings=settings,
        sm=make_sessionmaker(engine),
        keys=keys,
        hasher=PasswordHasher(settings),
        mailer=mailer or make_sender(settings, http),
        limiter=limiter or MemoryRateLimiter(),
        http=http,
        google=GoogleOidc(settings, http, keys) if settings.google_enabled else None,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await engine.dispose()
        if own_http:
            await http.aclose()

    app = FastAPI(
        title="PitchVision Identity",
        version=__version__,
        lifespan=lifespan,
        openapi_url=f"{API_PREFIX}/auth/openapi.json",
        docs_url=f"{API_PREFIX}/auth/docs",
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.ctx = ctx

    install_error_handlers(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,  # the refresh cookie
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type", REQUEST_ID_HEADER],
        expose_headers=[REQUEST_ID_HEADER],
    )
    app.add_middleware(RequestIdMiddleware)

    app.include_router(health.router)
    app.include_router(wellknown.router)
    app.include_router(api_router())
    app.include_router(internal.router)
    app.openapi = lambda: _openapi(app)  # type: ignore[method-assign]
    return app


def _openapi(app: FastAPI) -> dict[str, Any]:
    """Errors are problem+json everywhere: rewrite 4xx/5xx (incl. FastAPI's default 422) to ProblemDetails."""
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    ref = {"$ref": "#/components/schemas/ProblemDetails"}
    for ops in schema.get("paths", {}).values():
        for op in ops.values():
            for code, resp in op.get("responses", {}).items():
                if code[0] in "45":
                    resp["content"] = {PROBLEM_JSON: {"schema": ref}}
                    if code == "422":
                        resp["description"] = "Validation failed (`errors` lists the fields)"
    components = schema.setdefault("components", {}).setdefault("schemas", {})
    for unused in ("HTTPValidationError", "ValidationError"):
        components.pop(unused, None)
    app.openapi_schema = schema
    return schema


app = create_app()
