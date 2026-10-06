"""/api/v1/results/{video_id}/… — analysis results (M1-BE-15, AD §8.3, §10.2). Any org member (viewer+)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from pitchvision_schemas import Manifest

from pitchvision_api.deps import CurrentPrincipal, DbSession, SettingsDep
from pitchvision_api.schemas.results import TeamList, TrackPage
from pitchvision_api.services import results as service
from pitchvision_api.services.storage import Storage

router = APIRouter(prefix="/results", tags=["results"])


def _storage(request: Request) -> Storage:
    storage: Storage = request.app.state.storage
    return storage


@router.get("/{video_id}/manifest")
async def get_manifest(video_id: uuid.UUID, request: Request, response: Response, principal: CurrentPrincipal,
                       session: DbSession, settings: SettingsDep) -> Manifest:  # fmt: skip
    """What the player needs before fetching tiles (fps, size, shots, coverage, teams, pitch status) for the
    video's latest finished analysis. `tile_url_template` is signed and expires (`PV_TILE_URL_TTL_S`, 6 h): fetch
    the manifest again when a tile answers 403. 409 `reason: processing` while the first analysis is running."""
    response.headers["Cache-Control"] = "private, no-store"  # carries a signature
    return await service.manifest(session, _storage(request), settings, principal, video_id)


@router.get("/{video_id}/teams")
async def get_teams(video_id: uuid.UUID, principal: CurrentPrincipal, session: DbSession) -> TeamList:
    """Teams of the video's analysis (one per team-model cluster; name / colour editable later)."""
    return await service.teams(session, principal, video_id)


@router.get("/{video_id}/tracks")
async def get_tracks(
    video_id: uuid.UUID,
    principal: CurrentPrincipal,
    session: DbSession,
    from_ms: Annotated[int, Query(ge=0)] = 0,
    to_ms: Annotated[int | None, Query(gt=0, description="exclusive; default from_ms + 60 s; window ≤ 10 min")] = None,
    limit: Annotated[int, Query(ge=1, le=10_000)] = 2000,
    cursor: Annotated[str | None, Query(max_length=64)] = None,
) -> TrackPage:
    """5 Hz positions for analysts (AD §10.2) — the player uses the manifest + tiles instead. Keyset-paged."""
    return await service.tracks(session, principal, video_id, from_ms, to_ms, limit, cursor)
