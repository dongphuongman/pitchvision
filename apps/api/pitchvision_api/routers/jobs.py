"""/api/v1/jobs — analysis jobs (M1-BE-09, AD §6, §16.1). Live progress: WebSocket topic `job:{id}` (/api/v1/ws)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from pitchvision_api.deps import CurrentPrincipal, DbSession, EditorPrincipal
from pitchvision_api.schemas.jobs import JobCreate, JobList, JobOut
from pitchvision_api.services import jobs
from pitchvision_api.services.idempotency import Idem
from pitchvision_api.services.ratelimit import JOBS, rate_limited
from pitchvision_api.services.tasks import TaskQueue

router = APIRouter(tags=["jobs"])


def _tasks(request: Request) -> TaskQueue:
    tasks: TaskQueue = request.app.state.tasks
    return tasks


@router.post("/jobs", status_code=202, response_model=JobOut, dependencies=[Depends(rate_limited(JOBS))])
async def create_job(
    body: JobCreate, request: Request, principal: EditorPrincipal, session: DbSession, idem: Idem
) -> JSONResponse:
    """Start analysing a video. Requires `Idempotency-Key` (a replay returns the same job).

    The video must be `ready`, or a YouTube video still `ingesting` (the job then waits in status `ingesting`).
    409 CONFLICT: the video already has an active job (`job_id` extension) or isn't ready (`reason: video_not_ready`).
    """
    job = await jobs.create(session, _tasks(request), request.app.state.settings, principal, body)
    return await idem.respond(JobOut.of(job), status_code=202)


@router.get("/jobs/{job_id}")
async def get_job(job_id: uuid.UUID, principal: CurrentPrincipal, session: DbSession) -> JobOut:
    return JobOut.of(await jobs.get(session, principal, job_id))


@router.get("/videos/{video_id}/jobs")
async def list_video_jobs(
    video_id: uuid.UUID,
    principal: CurrentPrincipal,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> JobList:
    return JobList(items=[JobOut.of(j) for j in await jobs.for_video(session, principal, video_id, limit)])


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: uuid.UUID, request: Request, principal: EditorPrincipal, session: DbSession) -> JobOut:
    """Cancel a running job: no further stages are dispatched and queued tasks are revoked. 409 if already finished."""
    return JobOut.of(await jobs.cancel(session, _tasks(request), principal, job_id))


@router.post("/jobs/{job_id}/retry", status_code=202, dependencies=[Depends(rate_limited(JOBS))])
async def retry_job(job_id: uuid.UUID, request: Request, principal: EditorPrincipal, session: DbSession) -> JobOut:
    """Resume a `failed` job (with `error.retryable`) from the stage that failed; finished stages are skipped."""
    return JobOut.of(await jobs.retry(session, _tasks(request), principal, job_id))
