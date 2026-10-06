"""jobs / job_tasks persistence. Runs inside the caller's org-scoped transaction (RLS applies); queries still filter
on org_id explicitly (AD §14)."""

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from pitchvision_api.db.models import JOB_ACTIVE, Job, JobTask


async def get(session: AsyncSession, org_id: uuid.UUID, job_id: uuid.UUID, *, for_update: bool = False) -> Job | None:
    stmt = select(Job).where(Job.org_id == org_id, Job.id == job_id)
    if for_update:
        stmt = stmt.with_for_update()
    job: Job | None = await session.scalar(stmt.execution_options(populate_existing=True))
    return job


async def active_for_video(session: AsyncSession, org_id: uuid.UUID, video_id: uuid.UUID) -> Job | None:
    stmt = select(Job).where(Job.org_id == org_id, Job.video_id == video_id, Job.status.in_(JOB_ACTIVE)).limit(1)
    job: Job | None = await session.scalar(stmt)
    return job


async def for_video(session: AsyncSession, org_id: uuid.UUID, video_id: uuid.UUID, limit: int) -> list[Job]:
    stmt = (
        select(Job)
        .where(Job.org_id == org_id, Job.video_id == video_id)
        .order_by(Job.created_at.desc(), Job.id.desc())
        .limit(limit)
    )
    return list((await session.scalars(stmt)).all())


async def add(session: AsyncSession, job: Job) -> Job:
    session.add(job)
    await session.flush()
    await session.refresh(job)  # server defaults (id, created_at, status, progress)
    return job


async def reset_failed_tasks(session: AsyncSession, org_id: uuid.UUID, job_id: uuid.UUID) -> int:
    """Retry: failed / undispatched tasks go back to pending (attempts reset); done tasks are kept and skipped."""
    t = JobTask
    stmt = (
        update(t)
        .where(t.org_id == org_id, t.job_id == job_id, t.status != "done")
        .values(
            status="queued",
            attempts=0,
            error=None,
            external_id=None,
            not_before=None,
            dispatched_at=None,
            finished_at=None,
        )
    )
    result = await session.execute(stmt)
    return int(result.rowcount)  # type: ignore[attr-defined]
