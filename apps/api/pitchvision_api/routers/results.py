"""/api/v1/results/{video_id}/… — analysis results (M1-BE-15, AD §8.3, §10.2), team confirmation (SCR-14) and manual
pitch calibration (SCR-15, M1-BE-20). Reads: any org member (viewer+). Writes: editor+."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse
from pitchvision_schemas import Manifest, PitchTemplateInfo

from pitchvision_api.deps import CurrentPrincipal, DbSession, EditorPrincipal, SettingsDep
from pitchvision_api.schemas.jobs import JobOut
from pitchvision_api.schemas.results import (
    CalibrationAccepted,
    CalibrationIn,
    CalibrationState,
    FrameOut,
    ManualTeamsIn,
    MediaOut,
    TeamCandidates,
    TeamList,
    TeamsPatch,
    TrackPage,
)
from pitchvision_api.services import corrections
from pitchvision_api.services import results as service
from pitchvision_api.services.idempotency import Idem
from pitchvision_api.services.storage import Storage
from pitchvision_api.services.tasks import TaskQueue

router = APIRouter(prefix="/results", tags=["results"])


def _storage(request: Request) -> Storage:
    storage: Storage = request.app.state.storage
    return storage


def _tasks(request: Request) -> TaskQueue:
    tasks: TaskQueue = request.app.state.tasks
    return tasks


@router.get("/pitch-template")
async def get_pitch_template(response: Response, principal: CurrentPrincipal) -> PitchTemplateInfo:
    """The 32 pitch landmarks (index, name, kind, metres) + painted lines of the FIFA 105 × 68 template — SCR-15
    landmark menu (E03) and pitch diagram / line preview (E05). Static."""
    response.headers["Cache-Control"] = "private, max-age=86400"
    return service.pitch_template()


@router.get("/{video_id}/manifest")
async def get_manifest(video_id: uuid.UUID, request: Request, response: Response, principal: CurrentPrincipal,
                       session: DbSession, settings: SettingsDep) -> Manifest:  # fmt: skip
    """What the player needs before fetching tiles (fps, size, shots, coverage, teams, pitch status) for the
    video's latest finished analysis. `tile_url_template` is signed and expires (`PV_TILE_URL_TTL_S`, 6 h): fetch
    the manifest again when a tile answers 403. 409 `reason: processing` while the first analysis is running."""
    response.headers["Cache-Control"] = "private, no-store"  # carries a signature
    return await service.manifest(session, _storage(request), settings, principal, video_id)


@router.get("/{video_id}/media")
async def get_media(video_id: uuid.UUID, request: Request, response: Response, principal: CurrentPrincipal,
                    session: DbSession, settings: SettingsDep) -> MediaOut:  # fmt: skip
    """Presigned URL of the normalized video (25 fps) for the workspace player, valid `expires_in` s
    (`PV_MEDIA_URL_TTL_S`, 6 h) — fetch again on a media error. 404 `reason: not_normalized` before S2 wrote it."""
    response.headers["Cache-Control"] = "private, no-store"
    return await service.media(session, _storage(request), settings, principal, video_id)


@router.get("/{video_id}/teams")
async def get_teams(video_id: uuid.UUID, request: Request, principal: CurrentPrincipal, session: DbSession,
                    settings: SettingsDep) -> TeamList:  # fmt: skip
    """Teams of the video's analysis (one per team-model cluster) + sample crops per team: `sample_sheet.url` is
    one image (S3's crop grid) and each team's `samples` are rectangles in it (≤ 8)."""
    return await service.teams(session, _storage(request), settings, principal, video_id)


@router.patch("/{video_id}/teams")
async def patch_teams(video_id: uuid.UUID, body: TeamsPatch, request: Request, principal: EditorPrincipal,
                      session: DbSession, settings: SettingsDep) -> TeamList:  # fmt: skip
    """Confirm teams (SCR-14 E09): names (1–40 chars), colours (#rrggbb), optional swap of name + colour between the
    two clusters. Display-only — tiles keep the cluster number, the manifest maps it — so nothing is reprocessed.
    409 `reason: no_teams` before the analysis finished."""
    await corrections.patch_teams(session, principal, video_id, body)
    return await service.teams(session, _storage(request), settings, principal, video_id)


@router.get("/{video_id}/teams/candidates")
async def get_team_candidates(video_id: uuid.UUID, request: Request, principal: CurrentPrincipal,
                              session: DbSession) -> TeamCandidates:  # fmt: skip
    """Player boxes on team detection's sample frames (SCR-14 manual mode): show the frame
    (`GET …/calibration/frame?t_ms=`) and let the user click boxes per team. Also after TEAM_SEPARATION_FAILED."""
    return await service.candidates(session, _storage(request), principal, video_id)


@router.post("/{video_id}/teams/manual", status_code=202, response_model=JobOut)
async def post_manual_teams(video_id: uuid.UUID, body: ManualTeamsIn, request: Request, principal: EditorPrincipal,
                            session: DbSession, settings: SettingsDep, idem: Idem) -> JSONResponse:  # fmt: skip
    """Re-run team detection seeded with ≥ 2 picked players per team (SCR-14 E13): a new job of kind `team_rerun`
    (reuses normalize / shots of the latest job; re-runs calibrate → infer → …). Requires `Idempotency-Key`.
    409: an active job (`job_id`), or no analysis yet (`reason: no_analysis`)."""
    job = await corrections.manual_teams(session, _tasks(request), settings, principal, video_id, body)
    return await idem.respond(JobOut.of(job), status_code=202)


@router.get("/{video_id}/calibration")
async def get_calibration(video_id: uuid.UUID, principal: CurrentPrincipal, session: DbSession) -> CalibrationState:
    """Pitch calibration state for SCR-15: the latest analysis' status, a running job, the last manual points."""
    return await corrections.calibration_state(session, principal, video_id)


@router.post("/{video_id}/calibration", status_code=202, response_model=CalibrationAccepted)
async def post_calibration(video_id: uuid.UUID, body: CalibrationIn, request: Request, principal: EditorPrincipal,
                           session: DbSession, settings: SettingsDep, idem: Idem) -> JSONResponse:  # fmt: skip
    """Manual 4–8 point calibration (SCR-15 E09): starts a CPU-only `recalibrate` job (re-projection with the manual
    homography → tiles under a new revision → positions → new manifest; ~1–2 min). Track it like any job (WS topic
    `job:{id}`); the current manifest stays served until the new one is live. Requires `Idempotency-Key`.
    422 `reason`: frame_ms (points on different frames) · duplicate_landmark · collinear · not_analysed_shot."""
    accepted = await corrections.calibrate(session, _tasks(request), settings, principal, video_id, body)
    return await idem.respond(accepted, status_code=202)


@router.get("/{video_id}/calibration/frame")
async def get_calibration_frame(
    video_id: uuid.UUID,
    request: Request,
    response: Response,
    principal: CurrentPrincipal,
    session: DbSession,
    settings: SettingsDep,
    t_ms: Annotated[int, Query(ge=0, le=6 * 3600 * 1000)],
) -> FrameOut:
    """A still of the normalized video at `t_ms` (snapped to 25 fps) as a presigned JPEG URL (SCR-14 manual mode,
    SCR-15). Extracted by a worker on first use (may take a few seconds), cached afterwards. 503 when the worker
    is busy / down (retry), 404 `reason: no_video` before S2, 422 `reason: out_of_range` past the end."""
    response.headers["Cache-Control"] = "private, no-store"
    return await service.frame(session, _storage(request), _tasks(request), settings, principal, video_id, t_ms)


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
