"""Analysis results (teams, positions_org) — org-scoped sessions; queries still filter on org_id (AD §14)."""

import uuid

from sqlalchemy import select, text, tuple_
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from pitchvision_api.db.models import Job, Team, positions_org


async def latest_done_job(session: AsyncSession, org_id: uuid.UUID, video_id: uuid.UUID) -> Job | None:
    stmt = (
        select(Job)
        .where(Job.org_id == org_id, Job.video_id == video_id, Job.status == "done")
        .order_by(Job.finished_at.desc().nulls_last(), Job.created_at.desc(), Job.id.desc())
        .limit(1)
    )
    job: Job | None = await session.scalar(stmt)
    return job


async def teams(session: AsyncSession, org_id: uuid.UUID, video_id: uuid.UUID) -> list[Team]:
    stmt = select(Team).where(Team.org_id == org_id, Team.video_id == video_id).order_by(Team.cluster, Team.id)
    return list((await session.scalars(stmt)).all())


async def positions(
    session: AsyncSession,
    video_id: uuid.UUID,
    from_ms: int,
    to_ms: int,
    limit: int,
    after: tuple[int, int] | None = None,
) -> list[RowMapping]:
    """5 Hz rows in [from_ms, to_ms) ordered by (ts_ms, track_id), keyset-paged after ``(ts_ms, track_id)``.

    positions_org filters on app.org_id (the session's org); the hypertable itself is not readable by pv_app.
    """
    p = positions_org.c
    stmt = (
        select(p.ts_ms, p.track_id, p.team, p.px, p.py, p.vx, p.vy, p.q)
        .where(p.video_id == video_id, p.ts_ms >= from_ms, p.ts_ms < to_ms)
        .order_by(p.ts_ms, p.track_id)
        .limit(limit)
    )
    if after is not None:
        stmt = stmt.where(tuple_(p.ts_ms, p.track_id) > tuple_(*after))
    return list((await session.execute(stmt)).mappings().all())


async def job_org(session: AsyncSession, job_id: uuid.UUID) -> uuid.UUID | None:
    """org of a job without an org context (migration 0003 SECURITY DEFINER helper; ids only)."""
    org: uuid.UUID | None = await session.scalar(text("SELECT pv_job_org(:j)"), {"j": job_id})
    return org
