"""GET /api/v1/meta — build version and the shared-contract version the web client must match."""
from fastapi import APIRouter
from pitchvision_schemas import SCHEMA_VERSION
from pydantic import BaseModel

from pitchvision_api import __version__

router = APIRouter(tags=["meta"])


class Meta(BaseModel):
    version: str
    schema_version: int


@router.get("/meta")
async def meta() -> Meta:
    return Meta(version=__version__, schema_version=SCHEMA_VERSION)
