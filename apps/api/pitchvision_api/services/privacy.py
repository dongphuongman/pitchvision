"""SCR-40 Data & privacy, regional half: GDPR data export (D02) and the counts DLG-08 shows.

POST /privacy/data-exports records a ``data_exports`` row and enqueues ``cpu.build_data_export``; the worker zips the
account data (from identity-svc) + the org's data (admin+) or the caller's own rows, uploads it to
``keys.data_export_key`` and notifies ``data_export_ready`` (in-app + email). One active export per user and org.
Deleting an org / account happens in identity-svc; the worker purges this region (pitchvision_worker.privacy).
"""

import uuid
from datetime import UTC, datetime, timedelta

from pitchvision_schemas import ErrorCode
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pitchvision_api.auth import Principal
from pitchvision_api.db.models import DataExport, Export, Video
from pitchvision_api.errors import ApiError
from pitchvision_api.schemas.privacy import DataExportDownload, DataExportOut, PrivacySummary
from pitchvision_api.services.storage import Storage
from pitchvision_api.services.tasks import QUEUE_CPU, TASK_BUILD_DATA_EXPORT, TaskQueue, TaskUnavailable
from pitchvision_api.settings import Settings

STALE_AFTER = timedelta(hours=1)  # a queued / running export older than this (worker died) no longer blocks a new one


def _now() -> datetime:
    return datetime.now(UTC)


async def _latest(session: AsyncSession, principal: Principal) -> DataExport | None:
    return await session.scalar(
        select(DataExport).where(DataExport.org_id == principal.org_id, DataExport.user_id == principal.user_id)
        .order_by(DataExport.created_at.desc()).limit(1))  # fmt: skip


async def summary(session: AsyncSession, principal: Principal) -> PrivacySummary:
    videos = await session.scalar(select(func.count()).select_from(Video)
                                  .where(Video.org_id == principal.org_id, Video.status != "deleted"))  # fmt: skip
    exports = await session.scalar(select(func.count()).select_from(Export)
                                   .where(Export.org_id == principal.org_id, Export.deleted_at.is_(None)))  # fmt: skip
    latest = await _latest(session, principal)
    return PrivacySummary(videos=int(videos or 0), exports=int(exports or 0),
                          data_export=DataExportOut.of(latest, _now()) if latest else None)  # fmt: skip


async def create_data_export(session: AsyncSession, tasks: TaskQueue, principal: Principal) -> DataExportOut:
    """Admin+ get the whole org; other members their own data. 409 ``in_progress`` while one is queued / running
    (for up to ``STALE_AFTER``)."""
    latest = await _latest(session, principal)
    if latest is not None and latest.status in ("queued", "running") and latest.created_at > _now() - STALE_AFTER:
        raise ApiError(ErrorCode.CONFLICT, 409, "Export already in progress", "We'll email you when it is ready.",
                       extensions={"reason": "in_progress"})  # fmt: skip
    row = DataExport(org_id=principal.org_id, user_id=principal.user_id,
                     scope="org" if principal.has_role("admin") else "account")  # fmt: skip
    session.add(row)
    await session.flush()
    await session.refresh(row)
    try:
        await tasks.send(TASK_BUILD_DATA_EXPORT, {"export_id": str(row.id), "org_id": str(row.org_id)}, QUEUE_CPU)
    except TaskUnavailable:
        raise ApiError(ErrorCode.INTERNAL, 503, "Export queue unavailable", "Try again shortly.") from None
    return DataExportOut.of(row, _now())


def data_export_filename(d: DataExport) -> str:
    return f"pitchvision-data-{d.created_at:%Y-%m-%d}.zip"


async def download(session: AsyncSession, storage: Storage, settings: Settings, principal: Principal,
                   export_id: uuid.UUID) -> DataExportDownload:  # fmt: skip
    """The requester only. 409 ``not_ready``; 410 ``expired``."""
    d = await session.scalar(select(DataExport).where(DataExport.org_id == principal.org_id,
                                                      DataExport.id == export_id,
                                                      DataExport.user_id == principal.user_id))  # fmt: skip
    if d is None:
        raise ApiError(ErrorCode.NOT_FOUND, 404, "Data export not found")
    out = DataExportOut.of(d, _now())
    if out.status == "expired":
        raise ApiError(ErrorCode.NOT_FOUND, 410, "Data export expired", "Archives are kept for 7 days; request a "
                       "new one.", extensions={"reason": "expired"})  # fmt: skip
    if d.status != "done" or not d.object_key:
        raise ApiError(ErrorCode.CONFLICT, 409, "Data export not ready", f"The export is {d.status}.",
                       extensions={"reason": "not_ready"})  # fmt: skip
    name, ttl = data_export_filename(d), settings.presign_ttl_s
    return DataExportDownload(url=storage.presign_get(d.object_key, ttl, download_name=name), filename=name,
                              expires_in=ttl)  # fmt: skip
