"""Results read side (M1-BE-15/16, AD §8.3, §10.2): manifest, teams, tracks.

Manifest: the orchestrator writes ``media/…/manifest.json`` (keys.manifest_key) LAST, right before the job becomes
``done``; its ``tile_url_template`` holds the object-key template. Here we serve the manifest of the video's latest
``done`` job with:
- ``teams`` taken from the ``teams`` table when present (user renames / confirmations win over the job's defaults);
- ``tile_url_template`` replaced by a signed URL template for the tile route (services.tile_signing).

No done job → 409 CONFLICT ``reason: processing`` while a job is active, else 404. A manifest that belongs to a
different job than the latest done one (a newer job is finishing right now) is also ``processing``.
"""

import asyncio
import base64
import re
import time
import uuid

from pitchvision_schemas import ErrorCode, Manifest, TeamInfo, keys
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from pitchvision_api.auth import Principal
from pitchvision_api.errors import ApiError
from pitchvision_api.repositories import jobs as jobs_repo
from pitchvision_api.repositories import results as repo
from pitchvision_api.repositories import videos as videos_repo
from pitchvision_api.schemas.results import TeamList, TeamOut, TrackPage, TrackPoint
from pitchvision_api.services.storage import STORAGE_ERRORS, Storage, storage_error
from pitchvision_api.services.tile_signing import TILES_ROUTE, signed_template
from pitchvision_api.services.videos import not_found
from pitchvision_api.settings import Settings

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
MAX_TRACK_WINDOW_MS = 10 * 60 * 1000


def _processing(job_id: uuid.UUID | None) -> ApiError:
    ext = {"reason": "processing"} | ({"job_id": str(job_id)} if job_id else {})
    return ApiError(ErrorCode.CONFLICT, 409, "Analysis in progress", "The video has no finished analysis yet.",
                    extensions=ext)  # fmt: skip


async def _video_or_404(session: AsyncSession, principal: Principal, video_id: uuid.UUID) -> None:
    if await videos_repo.get(session, principal.org_id, video_id) is None:
        raise not_found()


async def manifest(session: AsyncSession, storage: Storage, settings: Settings, principal: Principal,
                   video_id: uuid.UUID) -> Manifest:  # fmt: skip
    await _video_or_404(session, principal, video_id)
    job = await repo.latest_done_job(session, principal.org_id, video_id)
    if job is None:
        active = await jobs_repo.active_for_video(session, principal.org_id, video_id)
        if active is not None:
            raise _processing(active.id)
        raise ApiError(ErrorCode.NOT_FOUND, 404, "No analysis result", "Start an analysis job for this video first.")
    try:
        raw = await asyncio.to_thread(storage.get_bytes, keys.manifest_key(str(principal.org_id), str(video_id)))
    except STORAGE_ERRORS as e:
        raise storage_error(e) from e
    if raw is None:
        raise ApiError(ErrorCode.NOT_FOUND, 404, "No analysis result", "The finished job left no result manifest.",
                       extensions={"reason": "manifest_missing"})  # fmt: skip
    try:
        m = Manifest.model_validate_json(raw)
    except ValidationError as e:
        raise ApiError(ErrorCode.INTERNAL, 500, "Result manifest unreadable") from e
    if m.job_id != str(job.id):
        raise _processing(None)
    db_teams = [t for t in await repo.teams(session, principal.org_id, video_id)
                if t.cluster is not None and t.name and t.color and _HEX.match(t.color)]  # fmt: skip
    if db_teams:
        m.teams = [TeamInfo(cluster=int(t.cluster), name=str(t.name)[:100], color=str(t.color),
                            confirmed=t.confirmed) for t in db_teams if t.cluster is not None]  # fmt: skip
    base = settings.tile_base_url or TILES_ROUTE
    m.tile_url_template = signed_template(settings.tile_key, base, video_id, m.job_id, m.revision, time.time(),
                                          settings.tile_url_ttl_s)  # fmt: skip
    return m


async def teams(session: AsyncSession, principal: Principal, video_id: uuid.UUID) -> TeamList:
    await _video_or_404(session, principal, video_id)
    return TeamList(items=[TeamOut.of(t) for t in await repo.teams(session, principal.org_id, video_id)])


def encode_cursor(ts_ms: int, track_id: int) -> str:
    return base64.urlsafe_b64encode(f"{ts_ms}:{track_id}".encode()).rstrip(b"=").decode()


def decode_cursor(cursor: str) -> tuple[int, int]:
    try:
        ts, tid = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode().split(":")
        return int(ts), int(tid)
    except (ValueError, UnicodeDecodeError) as e:
        raise ApiError(ErrorCode.VALIDATION_FAILED, 422, "Invalid cursor") from e


async def tracks(session: AsyncSession, principal: Principal, video_id: uuid.UUID, from_ms: int, to_ms: int | None,
                 limit: int, cursor: str | None) -> TrackPage:  # fmt: skip
    end = to_ms if to_ms is not None else from_ms + 60_000
    if end <= from_ms or end - from_ms > MAX_TRACK_WINDOW_MS:
        raise ApiError(ErrorCode.VALIDATION_FAILED, 422, "Invalid time window",
                       f"to_ms must be > from_ms and the window at most {MAX_TRACK_WINDOW_MS // 60000} minutes.")
    after = decode_cursor(cursor) if cursor else None
    await _video_or_404(session, principal, video_id)
    rows = await repo.positions(session, video_id, from_ms, end, limit + 1, after)
    items = [TrackPoint.model_validate(dict(r)) for r in rows[:limit]]
    nxt = encode_cursor(items[-1].ts_ms, items[-1].track_id) if len(rows) > limit and items else None
    return TrackPage(items=items, next_cursor=nxt)
