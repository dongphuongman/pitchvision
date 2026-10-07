"""Probes, outside /api/v1: /healthz = process alive (no deps), /readyz = can serve (DB + Redis reachable)."""
import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, Request
from pitchvision_schemas import ErrorCode
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from pitchvision_api.errors import ApiError, ProblemDetails

router = APIRouter(tags=["health"])
log = logging.getLogger(__name__)

READY_TIMEOUT_S = 2.0


class HealthStatus(BaseModel):
    status: Literal["ok"] = "ok"


@router.get("/healthz")
async def healthz() -> HealthStatus:
    return HealthStatus()


@router.get("/readyz", responses={503: {"model": ProblemDetails}})
async def readyz(request: Request) -> HealthStatus:
    engine: AsyncEngine = request.app.state.engine
    try:
        async with asyncio.timeout(READY_TIMEOUT_S), engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as e:
        log.warning("readyz: database check failed: %r", e)
        raise ApiError(ErrorCode.INTERNAL, 503, "Service not ready", "database unreachable") from None
    try:  # job / export / WebSocket requests need the broker
        async with asyncio.timeout(READY_TIMEOUT_S):
            await request.app.state.redis.ping()
    except Exception as e:
        log.warning("readyz: redis check failed: %r", e)
        raise ApiError(ErrorCode.INTERNAL, 503, "Service not ready", "redis unreachable") from None
    return HealthStatus()
