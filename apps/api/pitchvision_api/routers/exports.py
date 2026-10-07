"""/api/v1/exports — annotated video exports (SCR-25 / SCR-26, M1-BE-17, AD S11). Any org member may export; the
requester or an editor+ may cancel / retry / delete. Progress: poll GET /exports?status=active (no WS topic)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse

from pitchvision_api.deps import CurrentPrincipal, DbSession, SettingsDep
from pitchvision_api.repositories.exports import StatusFilter
from pitchvision_api.schemas.exports import ExportCreate, ExportDownload, ExportList, ExportOut
from pitchvision_api.services import exports as service
from pitchvision_api.services.idempotency import Idem
from pitchvision_api.services.ratelimit import EXPORTS, rate_limited
from pitchvision_api.services.storage import Storage
from pitchvision_api.services.tasks import TaskQueue

router = APIRouter(prefix="/exports", tags=["exports"])


def _storage(request: Request) -> Storage:
    storage: Storage = request.app.state.storage
    return storage


def _tasks(request: Request) -> TaskQueue:
    tasks: TaskQueue = request.app.state.tasks
    return tasks


@router.post("", status_code=202, response_model=ExportOut, dependencies=[Depends(rate_limited(EXPORTS))])
async def create_export(body: ExportCreate, request: Request, principal: CurrentPrincipal, session: DbSession,
                        settings: SettingsDep, idem: Idem) -> JSONResponse:  # fmt: skip
    """Render the video's latest finished analysis as an MP4 (SCR-25). Requires `Idempotency-Key`.
    Range omitted = full match. 402 PLAN_REQUIRED (`reason: export_quality`): 1080p needs Pro. 409 `no_analysis`.
    422 `reason`: `range` (outside the video), `no_pitch_map` (layout `video_map` without pitch calibration).
    Free plan exports carry a watermark (`watermark: true`)."""
    out = await service.create(session, _storage(request), _tasks(request), settings, principal, body)
    return await idem.respond(out, status_code=202)


@router.get("")
async def list_exports(principal: CurrentPrincipal, session: DbSession,
                       status: Annotated[StatusFilter, Query()] = "all",
                       limit: Annotated[int, Query(ge=1, le=100)] = 50,
                       cursor: Annotated[str | None, Query()] = None) -> ExportList:  # fmt: skip
    """SCR-26, newest first. `status`: all · active (queued / running) · done. A done export past `expires_at`
    reads `expired` (file deleted)."""
    return await service.page(session, principal, status, limit, cursor)


@router.get("/{export_id}")
async def get_export(export_id: uuid.UUID, principal: CurrentPrincipal, session: DbSession) -> ExportOut:
    return await service.get(session, principal, export_id)


@router.get("/{export_id}/download")
async def download_export(export_id: uuid.UUID, request: Request, response: Response, principal: CurrentPrincipal,
                          session: DbSession, settings: SettingsDep) -> ExportDownload:  # fmt: skip
    """Presigned download URL (attachment, `PV_PRESIGN_TTL_S`, 15 min). 409 `not_ready`; 410 `expired`."""
    response.headers["Cache-Control"] = "private, no-store"
    return await service.download(session, _storage(request), settings, principal, export_id)


@router.post("/{export_id}/cancel")
async def cancel_export(export_id: uuid.UUID, principal: CurrentPrincipal, session: DbSession) -> ExportOut:
    """Stop a queued / running export (409 `finished` otherwise)."""
    return await service.cancel(session, principal, export_id)


@router.post("/{export_id}/retry", status_code=202, dependencies=[Depends(rate_limited(EXPORTS))])
async def retry_export(export_id: uuid.UUID, request: Request, principal: CurrentPrincipal, session: DbSession
                       ) -> ExportOut:  # fmt: skip
    """Render a failed / cancelled export again with the same settings (409 `not_retryable` otherwise)."""
    return await service.retry(session, _tasks(request), principal, export_id)


@router.delete("/{export_id}", status_code=204)
async def delete_export(export_id: uuid.UUID, principal: CurrentPrincipal, session: DbSession) -> Response:
    """Remove from the list (stops it if active); the file is deleted by the worker's purge."""
    await service.delete(session, principal, export_id)
    return Response(status_code=204)
