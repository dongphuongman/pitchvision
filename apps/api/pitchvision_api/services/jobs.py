"""Jobs (M1-BE-09): create / cancel / retry with the AD §16.1 state machine; the orchestrator drives the rest.

- A job needs a `ready` video — or a YouTube video still `ingesting`: then the job is created anyway and waits in
  status `ingesting` until the download finishes (the ingest worker pokes the orchestrator; the periodic sweep is the
  fallback). An upload still `uploading` is 409: the browser knows when its upload completes.
- One non-terminal job per video (409 CONFLICT, backed by the partial unique index ux_jobs_video_active).
- `orchestrate.start_job` is published BEFORE our transaction commits (same as ingest): if the broker is down we
  answer 503 and roll back, so no job is stuck in `queued`; the orchestrator retries until the row is visible.
- Status changes go through pitchvision_schemas.can_transition only.
"""

import logging
import uuid
from datetime import UTC, datetime

from pitchvision_schemas import ErrorCode, JobStatus, PipelineConfig, can_transition
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from pitchvision_api.auth import Principal
from pitchvision_api.db.models import Job
from pitchvision_api.errors import ApiError
from pitchvision_api.repositories import jobs as repo
from pitchvision_api.repositories import videos as videos_repo
from pitchvision_api.schemas.jobs import JobCreate, job_error
from pitchvision_api.services.tasks import (
    QUEUE_ORCHESTRATE,
    TASK_CANCEL_JOB,
    TASK_START_JOB,
    TaskQueue,
    TaskUnavailable,
)
from pitchvision_api.settings import Settings

log = logging.getLogger(__name__)


def not_found() -> ApiError:
    return ApiError(ErrorCode.NOT_FOUND, 404, "Job not found")


def _queue_down() -> ApiError:
    return ApiError(ErrorCode.INTERNAL, 503, "Job queue unavailable", "Try again shortly.")


def _active_conflict(job: Job) -> ApiError:
    return ApiError(
        ErrorCode.CONFLICT,
        409,
        "Video already has an active job",
        f"Job {job.id} is {job.status}; cancel it or wait for it to finish.",
        extensions={"job_id": str(job.id)},
    )


def build_config(partial: dict[str, object] | None) -> PipelineConfig:
    try:
        return PipelineConfig.model_validate(partial or {})
    except ValidationError as e:
        errors = [{"loc": ["body", "config", *e_["loc"]], "msg": e_["msg"], "type": e_["type"]} for e_ in e.errors()]
        raise ApiError(
            ErrorCode.VALIDATION_FAILED,
            422,
            "Invalid pipeline config",
            f"{len(errors)} invalid field(s)",
            extensions={"errors": errors},
        ) from None


async def _start(tasks: TaskQueue, job: Job) -> None:
    try:
        await tasks.send(TASK_START_JOB, {"job_id": str(job.id), "org_id": str(job.org_id)}, QUEUE_ORCHESTRATE)
    except TaskUnavailable:
        raise _queue_down() from None


async def create(
    session: AsyncSession, tasks: TaskQueue, settings: Settings, principal: Principal, body: JobCreate
) -> Job:
    config = build_config(body.config)
    video = await videos_repo.get(session, principal.org_id, body.video_id, for_update=True)  # serialises creates
    if video is None:
        raise ApiError(ErrorCode.NOT_FOUND, 404, "Video not found")
    waiting_for_youtube = video.status == "ingesting" and video.source_type == "youtube"
    if video.status != "ready" and not waiting_for_youtube:
        raise ApiError(
            ErrorCode.CONFLICT,
            409,
            "Video is not ready",
            f"Video is {video.status}; a job needs a ready video (or a YouTube video being ingested).",
            extensions={"reason": "video_not_ready"},
        )
    if (active := await repo.active_for_video(session, principal.org_id, video.id)) is not None:
        raise _active_conflict(active)
    try:
        job = await repo.add(
            session,
            Job(
                video_id=video.id,
                org_id=principal.org_id,
                created_by=principal.user_id,
                image_tag=settings.image_tag,
                config=config.model_dump(mode="json"),
                status=JobStatus.queued.value,
            ),
        )
    except IntegrityError:  # lost a race on ux_jobs_video_active
        raise ApiError(ErrorCode.CONFLICT, 409, "Video already has an active job") from None
    await _start(tasks, job)
    return job


async def get(session: AsyncSession, principal: Principal, job_id: uuid.UUID) -> Job:
    job = await repo.get(session, principal.org_id, job_id)
    if job is None:
        raise not_found()
    return job


async def for_video(session: AsyncSession, principal: Principal, video_id: uuid.UUID, limit: int) -> list[Job]:
    if await videos_repo.get(session, principal.org_id, video_id) is None:
        raise ApiError(ErrorCode.NOT_FOUND, 404, "Video not found")
    return await repo.for_video(session, principal.org_id, video_id, limit)


async def cancel(session: AsyncSession, tasks: TaskQueue, principal: Principal, job_id: uuid.UUID) -> Job:
    job = await repo.get(session, principal.org_id, job_id, for_update=True)
    if job is None:
        raise not_found()
    if not can_transition(JobStatus(job.status), JobStatus.cancelled):
        raise ApiError(ErrorCode.CONFLICT, 409, "Job can't be cancelled", f"Job is already {job.status}.")
    await _cancel(session, tasks, job)
    return job


async def cancel_active_for_video(
    session: AsyncSession, tasks: TaskQueue, principal: Principal, video_id: uuid.UUID
) -> Job | None:
    """DELETE /videos/{id}: cancel the video's non-terminal job, if any (same path as POST /jobs/{id}/cancel)."""
    active = await repo.active_for_video(session, principal.org_id, video_id)
    if active is None:
        return None
    job = await repo.get(session, principal.org_id, active.id, for_update=True)
    if job is None or not can_transition(JobStatus(job.status), JobStatus.cancelled):
        return None
    await _cancel(session, tasks, job)
    return job


async def _cancel(session: AsyncSession, tasks: TaskQueue, job: Job) -> None:
    """→ cancelled; `orchestrate.cancel_job` revokes dispatched tasks and publishes the WS `cancelled` event."""
    job.status = JobStatus.cancelled.value
    job.finished_at = datetime.now(UTC)
    await session.flush()
    try:  # best effort: the orchestrator's sweep also revokes tasks of cancelled jobs
        await tasks.send(TASK_CANCEL_JOB, {"job_id": str(job.id), "org_id": str(job.org_id)}, QUEUE_ORCHESTRATE)
    except TaskUnavailable:
        log.warning("cancel %s: queue unavailable; the orchestrator sweep will revoke its tasks", job.id)


async def retry(session: AsyncSession, tasks: TaskQueue, principal: Principal, job_id: uuid.UUID) -> Job:
    """failed → queued; the orchestrator skips tasks already done and resumes from the stage that failed."""
    job = await repo.get(session, principal.org_id, job_id, for_update=True)
    if job is None:
        raise not_found()
    if not (job.status == JobStatus.failed and can_transition(JobStatus.failed, JobStatus.queued)):
        raise ApiError(ErrorCode.CONFLICT, 409, "Only failed jobs can be retried", f"Job is {job.status}.")
    err = job_error(job.error)
    if err is not None and not err.retryable:
        raise ApiError(
            ErrorCode.CONFLICT,
            409,
            "Job failure is not retryable",
            f"{err.code.value}: start a new job after fixing the cause.",
            extensions={"reason": err.code.value},
        )
    if (active := await repo.active_for_video(session, principal.org_id, job.video_id)) is not None:
        raise _active_conflict(active)
    await repo.reset_failed_tasks(session, principal.org_id, job.id)
    job.status = JobStatus.queued.value
    job.stage = None  # the WS snapshot / latest_job must not report the failed stage as running
    job.error = None
    job.finished_at = None
    try:
        await session.flush()
    except IntegrityError:
        raise ApiError(ErrorCode.CONFLICT, 409, "Video already has an active job") from None
    await _start(tasks, job)
    return job
