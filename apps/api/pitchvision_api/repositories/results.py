"""Analysis results (teams, positions_org) — org-scoped sessions; queries still filter on org_id (AD §14)."""

import uuid

from sqlalchemy import select, text, tuple_
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from pitchvision_api.db.models import Correction, Job, JobTask, Player, ShotRow, Team, positions_org


async def latest_done_job(session: AsyncSession, org_id: uuid.UUID, video_id: uuid.UUID) -> Job | None:
    stmt = (
        select(Job)
        .where(Job.org_id == org_id, Job.video_id == video_id, Job.status == "done")
        .order_by(Job.finished_at.desc().nulls_last(), Job.created_at.desc(), Job.id.desc())
        .limit(1)
    )
    job: Job | None = await session.scalar(stmt)
    return job


async def latest_terminal_job(session: AsyncSession, org_id: uuid.UUID, video_id: uuid.UUID) -> Job | None:
    """Newest done / failed job (the parent of a SCR-14 manual-teams re-run)."""
    stmt = (
        select(Job)
        .where(Job.org_id == org_id, Job.video_id == video_id, Job.status.in_(("done", "failed")))
        .order_by(Job.created_at.desc(), Job.id.desc())
        .limit(1)
    )
    job: Job | None = await session.scalar(stmt)
    return job


async def latest_calibrate_job(session: AsyncSession, org_id: uuid.UUID, video_id: uuid.UUID) -> Job | None:
    """Newest job whose S3 calibrate ran to an end (done or failed) — where SCR-14 candidates live."""
    stmt = (
        select(Job)
        .join(JobTask, JobTask.job_id == Job.id)
        .where(Job.org_id == org_id, Job.video_id == video_id, JobTask.task_key == "calibrate",
               JobTask.status.in_(("done", "failed")))
        .order_by(Job.created_at.desc(), Job.id.desc())
        .limit(1)
    )  # fmt: skip
    job: Job | None = await session.scalar(stmt)
    return job


async def normalize_media(session: AsyncSession, org_id: uuid.UUID, video_id: uuid.UUID) -> dict[str, object] | None:
    """S2's media manifest (fps, width, height, duration_ms, frame_count) of the video's newest normalize."""
    stmt = (
        select(JobTask.payload)
        .join(Job, Job.id == JobTask.job_id)
        .where(Job.org_id == org_id, Job.video_id == video_id, JobTask.task_key == "normalize",
               JobTask.status == "done")
        .order_by(JobTask.finished_at.desc().nulls_last())
        .limit(1)
    )  # fmt: skip
    payload = await session.scalar(stmt)
    media = (payload or {}).get("manifest") if isinstance(payload, dict) else None
    return media if isinstance(media, dict) else None


async def shots(session: AsyncSession, org_id: uuid.UUID, video_id: uuid.UUID) -> list[ShotRow]:
    stmt = select(ShotRow).where(ShotRow.org_id == org_id, ShotRow.video_id == video_id).order_by(ShotRow.t0_ms)
    return list((await session.scalars(stmt)).all())


async def latest_correction(session: AsyncSession, org_id: uuid.UUID, video_id: uuid.UUID, type_: str
                            ) -> Correction | None:  # fmt: skip
    stmt = (
        select(Correction)
        .where(Correction.org_id == org_id, Correction.video_id == video_id, Correction.type == type_)
        .order_by(Correction.created_at.desc(), Correction.revision.desc())
        .limit(1)
    )
    row: Correction | None = await session.scalar(stmt)
    return row


async def add_correction(session: AsyncSession, row: Correction) -> Correction:
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


async def teams(session: AsyncSession, org_id: uuid.UUID, video_id: uuid.UUID, *, for_update: bool = False
                ) -> list[Team]:  # fmt: skip
    stmt = select(Team).where(Team.org_id == org_id, Team.video_id == video_id).order_by(Team.cluster, Team.id)
    if for_update:
        stmt = stmt.with_for_update()
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


async def players(session: AsyncSession, org_id: uuid.UUID, video_id: uuid.UUID, roles: list[str], limit: int
                  ) -> list[tuple[Player, int | None]]:  # fmt: skip
    """(player, team cluster) longest on screen first; ``roles`` empty = all."""
    stmt = (select(Player, Team.cluster).outerjoin(Team, Team.id == Player.team_id)
            .where(Player.org_id == org_id, Player.video_id == video_id))  # fmt: skip
    if roles:
        stmt = stmt.where(Player.role.in_(roles))
    stmt = stmt.order_by(Player.frames.desc().nulls_last(), Player.track_id).limit(limit)
    return [(r[0], r[1]) for r in (await session.execute(stmt)).all()]


async def players_by_track(session: AsyncSession, org_id: uuid.UUID, video_id: uuid.UUID, track_ids: list[int]
                           ) -> list[Player]:  # fmt: skip
    stmt = (select(Player).where(Player.org_id == org_id, Player.video_id == video_id,
                                 Player.track_id.in_(track_ids)).with_for_update())  # fmt: skip
    return list((await session.scalars(stmt)).all())


async def confirmed_players(session: AsyncSession, org_id: uuid.UUID, video_id: uuid.UUID
                            ) -> list[tuple[Player, int | None]]:  # fmt: skip
    stmt = (select(Player, Team.cluster).outerjoin(Team, Team.id == Player.team_id)
            .where(Player.org_id == org_id, Player.video_id == video_id, Player.confirmed.is_(True))
            .order_by(Player.track_id))  # fmt: skip
    return [(r[0], r[1]) for r in (await session.execute(stmt)).all()]
