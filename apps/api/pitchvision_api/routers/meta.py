"""GET /api/v1/meta — build version, the shared-contract version the web client must match, and what the workers
can do (``pitch_model``: advertised by the orchestrator in Redis, ``pv:cap:pitch_model``, refreshed every sweep)."""
import logging

from fastapi import APIRouter, Request
from pitchvision_schemas import SCHEMA_VERSION
from pydantic import BaseModel

from pitchvision_api import __version__

router = APIRouter(tags=["meta"])


log = logging.getLogger(__name__)
CAP_PITCH_MODEL = "pv:cap:pitch_model"  # pitchvision_worker.orchestrator.tasks


class Meta(BaseModel):
    version: str
    schema_version: int
    pitch_model: bool = False  # automatic pitch mapping available ("Map the pitch", POST /results/{v}/repitch)


@router.get("/meta")
async def meta(request: Request) -> Meta:
    pitch = False
    try:
        pitch = (await request.app.state.redis.get(CAP_PITCH_MODEL)) in (b"1", "1")
    except Exception as e:  # Redis down: the feature is just hidden
        log.warning("meta: capabilities unavailable: %r", e)
    return Meta(version=__version__, schema_version=SCHEMA_VERSION, pitch_model=pitch)
