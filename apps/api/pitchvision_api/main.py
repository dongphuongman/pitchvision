"""App factory. `uvicorn pitchvision_api.main:app` (module-level app reads PV_* env)."""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

from pitchvision_api import __version__
from pitchvision_api.auth import DEV_ORG_HEADER, DEV_ROLE_HEADER, DEV_USER_HEADER, JwksClient
from pitchvision_api.db import make_engine, make_sessionmaker
from pitchvision_api.errors import PROBLEM_JSON, ProblemDetails, install_error_handlers
from pitchvision_api.middleware import REQUEST_ID_HEADER, RequestIdMiddleware
from pitchvision_api.routers import exports, health, ingest, jobs, meta, results, tiles, videos, ws
from pitchvision_api.services.events import EventHub
from pitchvision_api.services.idempotency import IDEMPOTENCY_HEADER, REPLAYED_HEADER, install_idempotency
from pitchvision_api.services.storage import Storage
from pitchvision_api.services.tasks import CeleryTaskQueue
from pitchvision_api.settings import Settings

API_PREFIX = "/api/v1"
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {"4XX": {"model": ProblemDetails}, "5XX": {"model": ProblemDetails}}


def api_router() -> APIRouter:
    """Everything under /api/v1. Feature routers (videos, jobs, …) are included here."""
    router = APIRouter(prefix=API_PREFIX, responses=ERROR_RESPONSES)
    router.include_router(meta.router)
    router.include_router(ingest.router)
    router.include_router(videos.router)
    router.include_router(jobs.router)
    router.include_router(results.router)
    router.include_router(exports.router)
    router.include_router(tiles.router)  # signature-authenticated (no bearer): the tile URL is the credential
    router.include_router(ws.router)  # WebSocket /api/v1/ws (not in OpenAPI; protocol in routers/ws.py)
    return router


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    engine = make_engine(settings.database_url)  # lazy: no connection until first use

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await app.state.events.close()
        await engine.dispose()

    app = FastAPI(
        title="PitchVision API",
        version=__version__,
        lifespan=lifespan,
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=f"{API_PREFIX}/docs",
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = make_sessionmaker(engine)
    app.state.storage = Storage.from_settings(settings)
    app.state.tasks = CeleryTaskQueue(settings.redis_url)  # lazy: no broker connection until first send
    app.state.events = EventHub(settings.redis_url)  # lazy: Redis pub/sub opens with the first WS subscription
    app.state.jwks = JwksClient(settings.jwks_url, timeout_s=settings.jwks_timeout_s, ttl_s=settings.jwks_cache_ttl_s)

    install_error_handlers(app)
    install_idempotency(app)
    dev_headers = [DEV_ORG_HEADER, DEV_USER_HEADER, DEV_ROLE_HEADER] if settings.dev_auth else []
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type", IDEMPOTENCY_HEADER, REQUEST_ID_HEADER, *dev_headers],
        expose_headers=[REQUEST_ID_HEADER, IDEMPOTENCY_HEADER, REPLAYED_HEADER],
    )
    app.add_middleware(RequestIdMiddleware)  # outermost of ours: CORS preflights get an id too

    app.include_router(health.router)
    app.include_router(api_router())
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
