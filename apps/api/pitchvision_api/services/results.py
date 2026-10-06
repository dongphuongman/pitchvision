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
import json
import math
import re
import time
import uuid
from typing import Any

from pitchvision_schemas import (
    ErrorCode,
    Manifest,
    PitchTemplateInfo,
    TeamInfo,
    TrackOverride,
    keys,
    load_pitch_template,
)
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from pitchvision_api.auth import Principal
from pitchvision_api.db.models import Job
from pitchvision_api.errors import ApiError
from pitchvision_api.repositories import jobs as jobs_repo
from pitchvision_api.repositories import results as repo
from pitchvision_api.repositories import videos as videos_repo
from pitchvision_api.schemas.results import (
    MAX_SAMPLES_PER_TEAM,
    CandidateFrame,
    FrameOut,
    MediaOut,
    SampleCrop,
    SampleSheet,
    TeamCandidates,
    TeamList,
    TeamOut,
    TrackPage,
    TrackPoint,
)
from pitchvision_api.services.storage import STORAGE_ERRORS, Storage, storage_error
from pitchvision_api.services.tasks import (
    QUEUE_CPU,
    TASK_EXTRACT_FRAME,
    TaskFailed,
    TaskQueue,
    TaskTimeout,
    TaskUnavailable,
)
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
                            confirmed=t.confirmed, attack_h1=t.attack_h1 if t.attack_h1 in ("left", "right") else None)
                   for t in db_teams if t.cluster is not None]  # type: ignore[arg-type]  # fmt: skip
    fixed = await repo.confirmed_players(session, principal.org_id, video_id)
    m.track_overrides = [TrackOverride(track_id=p.track_id, team=cluster if cluster in (0, 1) else -1,
                                       cls=ROLE_CLS.get(p.role or "player", 0)) for p, cluster in fixed]  # fmt: skip
    base = settings.tile_base_url or TILES_ROUTE
    m.tile_url_template = signed_template(settings.tile_key, base, video_id, m.job_id, m.revision, time.time(),
                                          settings.tile_url_ttl_s)  # fmt: skip
    return m


FPS = 25  # normalized timeline (S2)
ROLE_CLS = {"player": 0, "goalkeeper": 1, "referee": 2}  # players.role → ObjCls (tiles)
TEAM_MODEL = "team_model.json"
TEAM_GRID = "team_samples.jpg"
CANDIDATES = "team_candidates.json"


def task_origin(job: Job, task_key: str) -> str:
    """Job whose objects a task's outputs are: derived jobs inherit stages (``detail.inherit``)."""
    inherit = (job.detail or {}).get("inherit") or {}
    return str(inherit.get(task_key) or job.id)


def models_prefix(org_id: uuid.UUID, video_id: uuid.UUID, job: Job) -> str:
    return keys.job_results_prefix(str(org_id), str(video_id), task_origin(job, "calibrate")) + "models/"


def sample_rects(stats: dict[str, Any]) -> tuple[dict[int, list[SampleCrop]], tuple[int, int]]:
    """Per-cluster crop rectangles in team_samples.jpg (S3 ``stats.grid``; older models: the fixed crops_grid layout
    with rows team 0 / team 1 / no team and ``cluster_sizes`` counts)."""
    grid = stats.get("grid") if isinstance(stats.get("grid"), dict) else None
    if grid:
        tw, th, lw = int(grid["tile_w"]), int(grid["tile_h"]), int(grid["label_w"])
        rows = [(int(r.get("cluster", -1)), int(r.get("count", 0))) for r in grid.get("rows") or []]
        size = (int(grid.get("width") or lw + 16 * tw), int(grid.get("height") or th * max(len(rows), 1)))
    else:
        sizes = stats.get("cluster_sizes") or []
        if len(sizes) != 2:
            return {}, (0, 0)
        tw, th, lw = 48, 64, 120
        rows = [(0, min(int(sizes[0]), 16)), (1, min(int(sizes[1]), 16)), (-1, 0)]
        size = (lw + 16 * tw, 3 * th)
    out: dict[int, list[SampleCrop]] = {}
    for r, (cluster, count) in enumerate(rows):
        if cluster in (0, 1):
            out[cluster] = [SampleCrop(x=lw + k * tw, y=r * th, w=tw, h=th)
                            for k in range(min(count, MAX_SAMPLES_PER_TEAM))]  # fmt: skip
    return out, size


async def _get_json(storage: Storage, key: str) -> Any:
    try:
        raw = await asyncio.to_thread(storage.get_bytes, key)
        return json.loads(raw) if raw else None
    except (*STORAGE_ERRORS, ValueError):
        return None


async def teams(session: AsyncSession, storage: Storage, settings: Settings, principal: Principal,
                video_id: uuid.UUID) -> TeamList:  # fmt: skip
    await _video_or_404(session, principal, video_id)
    rows = await repo.teams(session, principal.org_id, video_id)
    job = await repo.latest_done_job(session, principal.org_id, video_id)
    rects: dict[int, list[SampleCrop]] = {}
    sheet = None
    if job is not None:
        prefix = models_prefix(principal.org_id, video_id, job)
        model = await _get_json(storage, prefix + TEAM_MODEL)
        if isinstance(model, dict):
            rects, (w, h) = sample_rects(model.get("stats") or {})
            if rects:
                sheet = SampleSheet(url=storage.presign_get(prefix + TEAM_GRID), expires_in=settings.presign_ttl_s,
                                    width=w, height=h)  # fmt: skip
    items = [TeamOut.of(t, rects.get(int(t.cluster)) if t.cluster is not None else None) for t in rows]
    return TeamList(items=items, job_id=job.id if job else None, sample_sheet=sheet)


async def candidates(session: AsyncSession, storage: Storage, principal: Principal, video_id: uuid.UUID
                     ) -> TeamCandidates:  # fmt: skip
    await _video_or_404(session, principal, video_id)
    job = await repo.latest_calibrate_job(session, principal.org_id, video_id)
    if job is not None:
        prefix = models_prefix(principal.org_id, video_id, job)
        for key in (prefix + CANDIDATES, prefix + "diagnostics/" + CANDIDATES):
            data = await _get_json(storage, key)
            if isinstance(data, dict):
                return TeamCandidates(job_id=job.id, fps=float(data.get("fps") or FPS),
                                      frames=[CandidateFrame.model_validate(f) for f in data.get("frames") or []])
    raise ApiError(ErrorCode.NOT_FOUND, 404, "No team candidates",
                   "Team detection hasn't sampled this video yet (or ran before candidates were recorded).")


def snap_ms(t_ms: int) -> int:
    """Same as the worker's frames.snap_ms: start of the nearest frame of the 25 fps normalized timeline."""
    return round(math.floor(t_ms * FPS / 1000 + 0.5) * 1000 / FPS)


async def frame(session: AsyncSession, storage: Storage, tasks: TaskQueue, settings: Settings, principal: Principal,
                video_id: uuid.UUID, t_ms: int) -> FrameOut:  # fmt: skip
    await _video_or_404(session, principal, video_id)
    org, vid = str(principal.org_id), str(video_id)
    t = snap_ms(t_ms)
    key = keys.frame_key(org, vid, t)
    try:
        cached = await asyncio.to_thread(storage.head_size, key)
    except STORAGE_ERRORS as e:
        raise storage_error(e) from e
    if cached is None:
        try:
            res = await tasks.call(TASK_EXTRACT_FRAME, {"org_id": org, "video_id": vid, "t_ms": t}, QUEUE_CPU,
                                   settings.frame_task_timeout_s)  # fmt: skip
        except (TaskUnavailable, TaskTimeout, TaskFailed) as e:
            raise ApiError(ErrorCode.INTERNAL, 503, "Frame extraction unavailable", "Try again shortly.",
                           extensions={"reason": type(e).__name__}) from None  # fmt: skip
        if not isinstance(res, dict) or not res.get("ok"):
            reason = str((res or {}).get("reason") if isinstance(res, dict) else "invalid")
            if reason == "no_video":
                raise ApiError(ErrorCode.NOT_FOUND, 404, "Video not normalized yet", extensions={"reason": reason})
            if reason == "out_of_range":
                raise ApiError(ErrorCode.VALIDATION_FAILED, 422, "t_ms is past the end of the video",
                               extensions={"reason": reason})  # fmt: skip
            raise ApiError(ErrorCode.INTERNAL, 503, "Frame extraction failed", extensions={"reason": reason})
        key, t = str(res["key"]), int(res.get("t_ms", t))
    return FrameOut(url=storage.presign_get(key), t_ms=t, expires_in=settings.presign_ttl_s)


async def media(session: AsyncSession, storage: Storage, settings: Settings, principal: Principal,
                video_id: uuid.UUID) -> MediaOut:  # fmt: skip
    video = await videos_repo.get(session, principal.org_id, video_id)
    if video is None:
        raise not_found()
    key = keys.normalized_key(str(principal.org_id), str(video_id))
    try:
        size = await asyncio.to_thread(storage.head_size, key)
    except STORAGE_ERRORS as e:
        raise storage_error(e) from e
    if size is None:
        raise ApiError(ErrorCode.NOT_FOUND, 404, "No playable video yet",
                       "The video is played from its normalized copy, written by the first analysis step.",
                       extensions={"reason": "not_normalized"})  # fmt: skip
    m = await repo.normalize_media(session, principal.org_id, video_id) or {}
    width, height = _num(m.get("width")), _num(m.get("height"))
    if not (width and height) and video.width and video.height:  # S2 scales the long side to 1280
        scale = 1280 / max(video.width, video.height)
        width, height = 2 * round(video.width * scale / 2), 2 * round(video.height * scale / 2)
    return MediaOut(video_url=storage.presign_get(key, settings.media_url_ttl_s), expires_in=settings.media_url_ttl_s,
                    fps=_num(m.get("fps")) or FPS, width=width or 0, height=height or 0)  # fmt: skip


def _num(v: object) -> int:
    try:
        return round(float(v))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def pitch_template() -> PitchTemplateInfo:
    return load_pitch_template()


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
