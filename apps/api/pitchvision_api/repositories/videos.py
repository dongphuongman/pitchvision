"""videos persistence. Runs inside the caller's org-scoped transaction (RLS applies); queries still filter
on org_id explicitly (AD §14: every query carries org_id, RLS is the second line of defence)."""

import base64
import json
import uuid
from datetime import datetime

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from pitchvision_api.db.models import Video


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


async def page(
    session: AsyncSession, org_id: uuid.UUID, limit: int, cursor: tuple[datetime, uuid.UUID] | None
) -> tuple[list[Video], str | None]:
    """Newest first, keyset pagination on (created_at, id) — matches ix_videos_org_created."""
    stmt = select(Video).where(Video.org_id == org_id, Video.status != "deleted")
    if cursor is not None:
        stmt = stmt.where(tuple_(Video.created_at, Video.id) < tuple_(*cursor))
    stmt = stmt.order_by(Video.created_at.desc(), Video.id.desc()).limit(limit + 1)
    rows = list((await session.scalars(stmt)).all())
    more = len(rows) > limit
    rows = rows[:limit]
    return rows, (encode_cursor(rows[-1]) if more and rows else None)


async def add(session: AsyncSession, video: Video) -> Video:
    session.add(video)
    await session.flush()
    await session.refresh(video)  # server defaults (id, created_at, status)
    return video
