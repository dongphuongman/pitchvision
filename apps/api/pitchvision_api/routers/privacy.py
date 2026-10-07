"""/api/v1/privacy — SCR-40 Data & privacy (D02 export all data; counts for DLG-08). Deleting an org / account is
identity-svc's (DELETE /orgs/{id}, DELETE /me); this region's data is purged by the worker within 24 h."""

import uuid

from fastapi import APIRouter, Depends, Request, Response

from pitchvision_api.deps import CurrentPrincipal, DbSession, SettingsDep
from pitchvision_api.schemas.privacy import DataExportDownload, DataExportOut, PrivacySummary
from pitchvision_api.services import privacy as service
from pitchvision_api.services.ratelimit import DATA_EXPORTS, rate_limited
from pitchvision_api.services.storage import Storage
from pitchvision_api.services.tasks import TaskQueue

router = APIRouter(prefix="/privacy", tags=["privacy"])


@router.get("")
async def get_summary(principal: CurrentPrincipal, session: DbSession) -> PrivacySummary:
    """Videos / exports the active org holds here (DLG-08) and the caller's latest data export (SCR-40 D02)."""
    return await service.summary(session, principal)


@router.post("/data-exports", status_code=202, dependencies=[Depends(rate_limited(DATA_EXPORTS))])
async def create_data_export(request: Request, principal: CurrentPrincipal, session: DbSession) -> DataExportOut:
    """Start a GDPR export: a zip of your account data, plus — for admins and owners — the org's videos, analyses,
    corrections and positions (not the video files). Emailed when ready; kept 7 days. 409 `in_progress`."""
    tasks: TaskQueue = request.app.state.tasks
    return await service.create_data_export(session, tasks, principal)


@router.get("/data-exports/{export_id}/download")
async def download_data_export(export_id: uuid.UUID, request: Request, response: Response,
                               principal: CurrentPrincipal, session: DbSession,
                               settings: SettingsDep) -> DataExportDownload:  # fmt: skip
    """Presigned URL (15 min), requester only. 409 `not_ready`; 410 `expired`."""
    response.headers["Cache-Control"] = "private, no-store"
    storage: Storage = request.app.state.storage
    return await service.download(session, storage, settings, principal, export_id)
