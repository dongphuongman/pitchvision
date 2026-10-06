"""Exports (SCR-25 / SCR-26, M1-BE-17, AD S11): annotated MP4 of the latest finished analysis.

POST freezes an ``ExportSpec`` (latest done job, range, layers, layout, size, plan watermark, team names / colours
from the teams table) and enqueues ``cpu.render_export {export_id, org_id}``; the worker renders and uploads to
``keys.export_key`` and keeps ``status`` / ``progress`` up to date (the web polls GET /exports while one is active).
Plan rules (pitchvision_schemas.plans): 1080p needs Pro+ (402 PLAN_REQUIRED), Free exports carry a watermark, the
file is kept ``export_ttl_days`` after it is done (then ``expired``; the worker's purge deletes it).
Any member may export (UIUX: Viewer = view + export); cancel / retry / delete: the requester or an editor+.
"""

import re
import uuid
from datetime import UTC, datetime

from pitchvision_schemas import TERMINAL_EXPORT, ErrorCode, ExportSpec
from sqlalchemy.ext.asyncio import AsyncSession

from pitchvision_api.auth import Principal
from pitchvision_api.db.models import Export
from pitchvision_api.errors import ApiError
from pitchvision_api.repositories import exports as repo
from pitchvision_api.repositories import results as results_repo
from pitchvision_api.repositories import videos as videos_repo
from pitchvision_api.repositories.exports import StatusFilter
from pitchvision_api.schemas.exports import ExportCreate, ExportDownload, ExportList, ExportOut
from pitchvision_api.services import results as results_service
from pitchvision_api.services.storage import Storage
from pitchvision_api.services.tasks import QUEUE_CPU, TASK_RENDER_EXPORT, TaskQueue, TaskUnavailable
from pitchvision_api.services.videos import not_found
from pitchvision_api.settings import Settings


def _now() -> datetime:
    return datetime.now(UTC)


def _not_found() -> ApiError:
    return ApiError(ErrorCode.NOT_FOUND, 404, "Export not found")


def _invalid(title: str, detail: str, reason: str) -> ApiError:
    return ApiError(ErrorCode.VALIDATION_FAILED, 422, title, detail, extensions={"reason": reason})


def _mmss(ms: int) -> str:
    s = ms // 1000
    return f"{s // 60:02d}m{s % 60:02d}"


def export_filename(title: str | None, from_ms: int, to_ms: int, duration_ms: int, ext: str = "mp4") -> str:
    """``fra_sui_full.mp4`` / ``fra_sui_34m00-34m52.mp4`` (SCR-26 "File")."""
    slug = re.sub(r"[^a-z0-9]+", "_", (title or "").lower()).strip("_")[:60] or "video"
    span = "full" if from_ms == 0 and to_ms >= duration_ms else f"{_mmss(from_ms)}-{_mmss(to_ms)}"
    return f"{slug}_{span}.{ext}"


async def _enqueue(tasks: TaskQueue, e: Export) -> None:
    try:
        await tasks.send(TASK_RENDER_EXPORT, {"export_id": str(e.id), "org_id": str(e.org_id)}, QUEUE_CPU)
    except TaskUnavailable:
        raise ApiError(ErrorCode.INTERNAL, 503, "Export queue unavailable", "Try again shortly.") from None


async def create(session: AsyncSession, storage: Storage, tasks: TaskQueue, settings: Settings, principal: Principal,
                 body: ExportCreate) -> ExportOut:  # fmt: skip
    video = await videos_repo.get(session, principal.org_id, body.video_id)
    if video is None:
        raise not_found()
    limits = principal.limits
    if body.quality > limits.export_max_height:
        raise ApiError(ErrorCode.PLAN_REQUIRED, 402, "Upgrade to export in 1080p",
                       f"Your {principal.plan.value.capitalize()} plan exports up to {limits.export_max_height}p.",
                       extensions={"reason": "export_quality", "plan": principal.plan.value})  # fmt: skip
    if await results_repo.latest_done_job(session, principal.org_id, video.id) is None:
        raise ApiError(ErrorCode.CONFLICT, 409, "No finished analysis", "Exports draw a finished analysis.",
                       extensions={"reason": "no_analysis"})  # fmt: skip
    m = await results_service.manifest(session, storage, settings, principal, video.id)
    from_ms = body.from_ms or 0
    to_ms = min(body.to_ms or m.duration_ms, m.duration_ms)
    if from_ms >= to_ms:
        raise _invalid("Empty range", f"The range must lie inside the video (0–{m.duration_ms} ms).", "range")
    if body.layout == "video_map" and m.pitch.status == "unavailable":
        raise _invalid("No tactical map", "This analysis has no pitch calibration, so there is no map to show "
                       "next to the video.", "no_pitch_map")  # fmt: skip
    spec = ExportSpec(kind=body.kind, job_id=m.job_id, revision=m.revision, from_ms=from_ms, to_ms=to_ms, fps=m.fps,
                      width=m.width, height=m.height, out_height=body.quality, layout=body.layout, layers=body.layers,
                      watermark=limits.export_watermark, ttl_days=limits.export_ttl_days, teams=m.teams,
                      track_overrides=m.track_overrides,
                      pitch_status=m.pitch.status)  # fmt: skip
    name = export_filename(video.title, from_ms, to_ms, m.duration_ms)
    row = await repo.add(session, Export(org_id=principal.org_id, video_id=video.id, kind=body.kind, filename=name,
                                         spec=spec.model_dump(mode="json"), created_by=principal.user_id))  # fmt: skip
    await _enqueue(tasks, row)  # the worker waits for the row if it is faster than this commit
    return ExportOut.of(row, _now(), video.title)


async def get(session: AsyncSession, principal: Principal, export_id: uuid.UUID) -> ExportOut:
    found = await repo.get(session, principal.org_id, export_id)
    if found is None:
        raise _not_found()
    return ExportOut.of(found[0], _now(), found[1])


async def page(session: AsyncSession, principal: Principal, status: StatusFilter, limit: int, cursor: str | None
               ) -> ExportList:  # fmt: skip
    try:
        after = repo.decode_cursor(cursor) if cursor else None
    except ValueError:
        raise _invalid("Invalid cursor", "Use next_cursor from the previous page.", "cursor") from None
    rows, nxt = await repo.page(session, principal.org_id, status, limit, after)
    now = _now()
    return ExportList(items=[ExportOut.of(e, now, title) for e, title in rows], next_cursor=nxt)


async def _owned(session: AsyncSession, principal: Principal, export_id: uuid.UUID) -> tuple[Export, str | None]:
    found = await repo.get(session, principal.org_id, export_id, for_update=True)
    if found is None:
        raise _not_found()
    if found[0].created_by != principal.user_id and not principal.has_role("editor"):
        raise ApiError(ErrorCode.FORBIDDEN, 403, "Not your export", "Only its requester or an editor can change it.")
    return found


async def cancel(session: AsyncSession, principal: Principal, export_id: uuid.UUID) -> ExportOut:
    e, title = await _owned(session, principal, export_id)
    if e.status in TERMINAL_EXPORT:
        raise ApiError(ErrorCode.CONFLICT, 409, "Export already finished", f"The export is {e.status}.",
                       extensions={"reason": "finished"})  # fmt: skip
    e.status, e.finished_at = "cancelled", _now()  # the worker checks the row between frames and stops
    await session.flush()
    return ExportOut.of(e, _now(), title)


async def retry(session: AsyncSession, tasks: TaskQueue, principal: Principal, export_id: uuid.UUID) -> ExportOut:
    e, title = await _owned(session, principal, export_id)
    if e.status not in ("failed", "cancelled"):
        raise ApiError(ErrorCode.CONFLICT, 409, "Nothing to retry", f"The export is {e.status}.",
                       extensions={"reason": "not_retryable"})  # fmt: skip
    e.status, e.progress, e.error = "queued", 0, None  # type: ignore[assignment]
    e.started_at = e.heartbeat_at = e.finished_at = e.expires_at = None
    await session.flush()
    await _enqueue(tasks, e)
    return ExportOut.of(e, _now(), title)


async def delete(session: AsyncSession, principal: Principal, export_id: uuid.UUID) -> None:
    """Hidden at once; an active render stops; the worker's purge deletes the file."""
    e, _ = await _owned(session, principal, export_id)
    now = _now()
    if e.status not in TERMINAL_EXPORT:
        e.status, e.finished_at = "cancelled", now
    e.deleted_at = now
    await session.flush()


async def download(session: AsyncSession, storage: Storage, settings: Settings, principal: Principal,
                   export_id: uuid.UUID) -> ExportDownload:  # fmt: skip
    found = await repo.get(session, principal.org_id, export_id)
    if found is None:
        raise _not_found()
    e = found[0]
    out = ExportOut.of(e, _now(), found[1])
    if out.status == "expired" or (e.status == "done" and not e.object_key):
        raise ApiError(ErrorCode.NOT_FOUND, 410, "Export expired", "Exports are kept for a limited time; start a "
                       "new one.", extensions={"reason": "expired"})  # fmt: skip
    if e.status != "done" or not e.object_key:
        raise ApiError(ErrorCode.CONFLICT, 409, "Export not ready", f"The export is {e.status}.",
                       extensions={"reason": "not_ready"})  # fmt: skip
    ttl = settings.presign_ttl_s
    return ExportDownload(url=storage.presign_get(e.object_key, ttl, download_name=e.filename), filename=e.filename,
                          expires_in=ttl)  # fmt: skip
