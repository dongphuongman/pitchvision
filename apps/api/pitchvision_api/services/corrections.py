"""User corrections that change results (AD §8.4, §16): SCR-14 team confirmation / manual team samples, SCR-15 manual
pitch calibration. Every change is recorded in ``corrections`` (who, what, at which revision).

- Teams (PATCH): name / colour / confirmed, and ``swap`` = exchange name + colour between clusters 0 and 1. Tiles and
  positions carry the CLUSTER (``team`` 0 / 1), the manifest's ``teams`` (served from this table) maps cluster →
  name / colour, so a rename or a swap is display-only: no reprocessing, no revision bump.
- Manual team samples (POST, TEAM_SEPARATION_FAILED or "these groups mix both teams"): a NEW job of kind
  ``team_rerun`` reusing the parent's normalize / shots / plan; S3 calibrate fits from the user's seeds, then S4…
- Manual calibration (POST): the API validates the points (≥ 4, distinct landmarks, 4 of them in general position in
  both the image and the template — the homography itself is computed by the worker, the API never imports cv_core),
  bumps ``videos.revision``, records the correction and starts a ``recalibrate`` job (CPU only): re-projection with
  the manual H → tiles under the new revision → load-db → new manifest. The old manifest / tiles stay served until
  the new manifest is written (the API serves the latest DONE job's manifest).
"""

import uuid
from itertools import combinations
from typing import Any

from pitchvision_schemas import ErrorCode, PipelineConfig, load_pitch_template
from sqlalchemy.ext.asyncio import AsyncSession

from pitchvision_api.auth import Principal
from pitchvision_api.db.models import Correction, Job, Video
from pitchvision_api.errors import ApiError
from pitchvision_api.repositories import jobs as jobs_repo
from pitchvision_api.repositories import results as repo
from pitchvision_api.repositories import videos as videos_repo
from pitchvision_api.schemas.jobs import JobOut
from pitchvision_api.schemas.results import (
    CalibrationAccepted,
    CalibrationIn,
    CalibrationPoint,
    CalibrationState,
    ManualCalibration,
    ManualTeamsIn,
    TeamsPatch,
)
from pitchvision_api.services import jobs as jobs_service
from pitchvision_api.services.tasks import TaskQueue
from pitchvision_api.services.videos import not_found
from pitchvision_api.settings import Settings

COLLINEAR_TOL = 0.01  # |cross| / longest side² below this = three points on one line


def _invalid(title: str, detail: str, reason: str) -> ApiError:
    return ApiError(ErrorCode.VALIDATION_FAILED, 422, title, detail, extensions={"reason": reason})


def _no_analysis(detail: str) -> ApiError:
    return ApiError(ErrorCode.CONFLICT, 409, "No finished analysis", detail, extensions={"reason": "no_analysis"})


async def _locked_video(session: AsyncSession, principal: Principal, video_id: uuid.UUID) -> Video:
    video = await videos_repo.get(session, principal.org_id, video_id, for_update=True)
    if video is None:
        raise not_found()
    return video


async def _record(session: AsyncSession, principal: Principal, video: Video, type_: str, payload: dict[str, Any]
                  ) -> Correction:  # fmt: skip
    row = Correction(video_id=video.id, org_id=principal.org_id, revision=video.revision, type=type_,
                     payload=payload, created_by=principal.user_id)  # fmt: skip
    return await repo.add_correction(session, row)


# ---------------------------------------------------------------- SCR-14 confirm
async def patch_teams(session: AsyncSession, principal: Principal, video_id: uuid.UUID, body: TeamsPatch) -> None:
    video = await _locked_video(session, principal, video_id)
    rows = await repo.teams(session, principal.org_id, video_id, for_update=True)
    if not rows:
        raise ApiError(ErrorCode.CONFLICT, 409, "No teams yet", "Teams exist once the analysis has finished.",
                       extensions={"reason": "no_teams"})  # fmt: skip
    by_cluster = {t.cluster: t for t in rows if t.cluster is not None}
    for edit in body.teams:
        team = by_cluster.get(edit.cluster)
        if team is None:
            raise _invalid("Unknown team", f"The analysis has no team cluster {edit.cluster}.", "unknown_cluster")
        team.name, team.color = edit.name, edit.color.lower()
    if body.swap:
        a, b = by_cluster.get(0), by_cluster.get(1)
        if a is None or b is None:
            raise _invalid("Nothing to swap", "Swapping needs both team clusters.", "unknown_cluster")
        a.name, b.name = b.name, a.name
        a.color, b.color = b.color, a.color
    for team in rows:
        team.confirmed = body.confirmed
    await session.flush()
    await _record(session, principal, video, "teams", {
        "teams": [{"cluster": t.cluster, "name": t.name, "color": t.color} for t in rows],
        "edits": [e.model_dump() for e in body.teams], "swap": body.swap, "confirmed": body.confirmed})


# ---------------------------------------------------------------- SCR-14 manual
async def manual_teams(session: AsyncSession, tasks: TaskQueue, settings: Settings, principal: Principal,
                       video_id: uuid.UUID, body: ManualTeamsIn) -> Job:  # fmt: skip
    video = await _locked_video(session, principal, video_id)
    await jobs_service.ensure_no_active(session, principal, video.id)
    parent = await repo.latest_terminal_job(session, principal.org_id, video_id)
    if parent is None:
        raise _no_analysis("Run an analysis first; manual team samples re-run its team detection.")
    seeds = {team: [s.model_dump(mode="json") for s in picks] for team, picks in sorted(body.samples.items())}
    corr = await _record(session, principal, video, "team_samples", {"samples": seeds, "parent_job_id": str(parent.id)})
    job = await jobs_service.create_derived(session, tasks, settings, principal, video, parent, "team_rerun",
                                            {"team_seeds": seeds, "correction_id": str(corr.id)})  # fmt: skip
    corr.payload = {**corr.payload, "job_id": str(job.id)}
    await session.flush()
    return job


# ---------------------------------------------------------------- SCR-15
def _collinear(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> bool:
    cross = abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))
    scale = max((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 for p, q in ((a, b), (a, c), (b, c)))
    return scale == 0 or cross / scale < COLLINEAR_TOL


def general_position(pts: list[tuple[float, float]]) -> bool:
    """True when some 4 of the points have no 3 on one line (what a homography needs)."""
    return any(all(not _collinear(pts[i], pts[j], pts[k]) for i, j, k in combinations(quad, 3))
               for quad in combinations(range(len(pts)), 4))  # fmt: skip


def validate_points(points: list[CalibrationPoint]) -> int:
    """→ the frame (ms) the points were clicked on; 422 on an unusable set."""
    frames = {p.frame_ms for p in points}
    if len(frames) != 1:
        raise _invalid("Points from several frames", "All points must be clicked on the same frame.", "frame_ms")
    idx = [p.template_index for p in points]
    if len(set(idx)) != len(idx):
        raise _invalid("Duplicate landmark", "Each pitch landmark can be used once.", "duplicate_landmark")
    tpl = {k.index: (k.x, k.y) for k in load_pitch_template().keypoints}
    image = [(p.x, p.y) for p in points]
    pitch = [tpl[i] for i in idx]
    if not general_position(image) or not general_position(pitch):
        raise _invalid("Points are collinear", "Pick at least 4 points, no 3 of them on one straight line (on the "
                       "frame and on the pitch diagram).", "collinear")  # fmt: skip
    return frames.pop()


async def calibrate(session: AsyncSession, tasks: TaskQueue, settings: Settings, principal: Principal,
                    video_id: uuid.UUID, body: CalibrationIn) -> CalibrationAccepted:  # fmt: skip
    frame_ms = validate_points(body.points)
    video = await _locked_video(session, principal, video_id)
    await jobs_service.ensure_no_active(session, principal, video.id)
    parent = await repo.latest_done_job(session, principal.org_id, video_id)
    if parent is None:
        raise _no_analysis("Manual calibration re-projects a finished analysis.")
    analyse = {str(t) for t in PipelineConfig.model_validate(parent.config).analyze_shot_types}
    shot = next((s for s in await repo.shots(session, principal.org_id, video_id) if s.t0_ms <= frame_ms < s.t1_ms),
                None)  # fmt: skip
    if shot is None or shot.type not in analyse:
        raise _invalid("Frame not analysed", f"Pick a frame inside an analysed ({', '.join(sorted(analyse))}) shot.",
                       "not_analysed_shot")  # fmt: skip
    video.revision = int(video.revision) + 1  # the recompute's tiles go under this revision
    calibration = {"points": [p.model_dump() for p in body.points], "frame_ms": frame_ms, "scope": body.scope}
    corr = await _record(session, principal, video, "calibration", {**calibration, "parent_job_id": str(parent.id),
                                                                     "shot": [shot.t0_ms, shot.t1_ms]})
    job = await jobs_service.create_derived(session, tasks, settings, principal, video, parent, "recalibrate",
                                            {"calibration": calibration, "correction_id": str(corr.id)})  # fmt: skip
    corr.payload = {**corr.payload, "job_id": str(job.id)}
    await session.flush()
    return CalibrationAccepted(job=JobOut.of(job), correction_id=corr.id, revision=video.revision)


async def calibration_state(session: AsyncSession, principal: Principal, video_id: uuid.UUID) -> CalibrationState:
    if await videos_repo.get(session, principal.org_id, video_id) is None:
        raise not_found()
    done = await repo.latest_done_job(session, principal.org_id, video_id)
    active = await jobs_repo.active_for_video(session, principal.org_id, video_id)
    last = await repo.latest_correction(session, principal.org_id, video_id, "calibration")
    status = (done.detail or {}).get("pitch_calibration") if done else None
    manual = None
    if last is not None:
        p = last.payload or {}
        manual = ManualCalibration(correction_id=last.id, revision=last.revision, scope=p.get("scope", "shot"),
                                   frame_ms=int(p.get("frame_ms") or 0), created_at=last.created_at,
                                   points=[CalibrationPoint.model_validate(x) for x in p.get("points") or []])
    return CalibrationState(
        pitch_calibration=status if status in ("ok", "degraded", "manual", "unavailable") else None,
        job_id=done.id if done else None, active_job=JobOut.of(active) if active else None, last_manual=manual)
