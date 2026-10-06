"""videos persistence. Runs inside the caller's org-scoped transaction (RLS applies); queries still filter
on org_id explicitly (AD §14: every query carries org_id, RLS is the second line of defence)."""

import base64
import json
import uuid
from datetime import datetime

from sqlalchemy import Select, select, true, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from pitchvision_api.db.models import Job, Video

VideoWithJob = tuple[Video, Job | None]


def encode_cursor(v: Video) -> str:
    raw = json.dumps([v.created_at.isoformat(), str(v.id)]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """Raises ValueError on anything that isn't a cursor we issued."""
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        created_at, vid = json.loads(raw)
        return datetime.fromisoformat(created_at), uuid.UUID(vid)
    except (ValueError, TypeError) as e:
        raise ValueError("invalid cursor") from e


async def get(
    session: AsyncSession, org_id: uuid.UUID, video_id: uuid.UUID, *, for_update: bool = False
) -> Video | None:
    stmt = select(Video).where(Video.org_id == org_id, Video.id == video_id, Video.status != "deleted")
    if for_update:
        stmt = stmt.with_for_update()
    video: Video | None = await session.scalar(stmt)
    return video


def _with_latest_job(org_id: uuid.UUID) -> Select[Video, Job]:
    """videos LEFT JOIN LATERAL (newest job of each video): one round trip for a page, no N+1 (ix_jobs_video)."""
    newest = (
        select(Job)
        .where(Job.video_id == Video.id, Job.org_id == org_id)
        .order_by(Job.created_at.desc(), Job.id.desc())
        .limit(1)
        .lateral("latest_job")
    )
    latest = aliased(Job, newest)
    return (
        select(Video, latest)
        .outerjoin(newest, true())
        .where(Video.org_id == org_id, Video.status != "deleted", Video.deleted_at.is_(None))
    )


async def get_with_latest_job(session: AsyncSession, org_id: uuid.UUID, video_id: uuid.UUID) -> VideoWithJob | None:
    row = (await session.execute(_with_latest_job(org_id).where(Video.id == video_id))).first()
    return (row[0], row[1]) if row else None


async def page(
    session: AsyncSession, org_id: uuid.UUID, limit: int, cursor: tuple[datetime, uuid.UUID] | None
) -> tuple[list[VideoWithJob], str | None]:
    """Newest first, keyset pagination on (created_at, id) — matches ix_videos_org_created. Each video comes with
    its newest job (or None)."""
    stmt = _with_latest_job(org_id)
    if cursor is not None:
        stmt = stmt.where(tuple_(Video.created_at, Video.id) < tuple_(*cursor))
    stmt = stmt.order_by(Video.created_at.desc(), Video.id.desc()).limit(limit + 1)
    rows: list[VideoWithJob] = [(r[0], r[1]) for r in (await session.execute(stmt)).all()]
    more = len(rows) > limit
    rows = rows[:limit]
    return rows, (encode_cursor(rows[-1][0]) if more and rows else None)


async def add(session: AsyncSession, video: Video) -> Video:
    session.add(video)
    await session.flush()
    await session.refresh(video)  # server defaults (id, created_at, status)
    return video
