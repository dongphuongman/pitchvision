"""SCR-51 notifications: the caller's own rows in the active org (RLS + user_id filter). Rows are written by the
worker (job / export outcomes, pitchvision_worker.notify) and by the quota check (80 % / 100 % of the month)."""

import uuid
from datetime import UTC, datetime
from typing import Any

from pitchvision_schemas import EMAIL_TYPES, ErrorCode
from sqlalchemy import func, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from pitchvision_api.auth import Principal
from pitchvision_api.db.models import Notification, Video
from pitchvision_api.errors import ApiError
from pitchvision_api.repositories.videos import decode_cursor, encode_cursor
from pitchvision_api.schemas.notifications import MarkRead, NotificationList, NotificationOut


def _mine(principal: Principal) -> list[Any]:
    return [Notification.org_id == principal.org_id, Notification.user_id == principal.user_id]


async def page(session: AsyncSession, principal: Principal, limit: int, cursor: str | None) -> NotificationList:
    stmt = (select(Notification, Video.title).outerjoin(Video, Video.id == Notification.video_id)
            .where(*_mine(principal)))  # fmt: skip
    if cursor:
        try:
            stmt = stmt.where(tuple_(Notification.created_at, Notification.id) < tuple_(*decode_cursor(cursor)))
        except ValueError:
            raise ApiError(ErrorCode.VALIDATION_FAILED, 422, "Invalid cursor",
                           extensions={"reason": "cursor"}) from None  # fmt: skip
    stmt = stmt.order_by(Notification.created_at.desc(), Notification.id.desc()).limit(limit + 1)
    rows = (await session.execute(stmt)).all()
    more = len(rows) > limit
    rows = rows[:limit]
    unread = await session.scalar(select(func.count()).select_from(Notification)
                                  .where(*_mine(principal), Notification.read_at.is_(None)))  # fmt: skip
    return NotificationList(items=[NotificationOut.of(n, title) for n, title in rows], unread_count=int(unread or 0),
                            next_cursor=encode_cursor(rows[-1][0]) if more and rows else None)  # type: ignore[arg-type]


async def mark_read(session: AsyncSession, principal: Principal, body: MarkRead) -> None:
    stmt = update(Notification).where(*_mine(principal), Notification.read_at.is_(None))
    if not body.all:
        if not body.ids:
            return
        stmt = stmt.where(Notification.id.in_(body.ids))
    await session.execute(stmt.values(read_at=datetime.now(UTC)))


async def add(session: AsyncSession, principal: Principal, type_: str, data: dict[str, Any], dedupe_key: str,
              video_id: uuid.UUID | None = None) -> None:  # fmt: skip
    """Insert for the caller unless (user, dedupe_key) exists."""
    stmt = pg_insert(Notification).values(
        org_id=principal.org_id, user_id=principal.user_id, type=type_, video_id=video_id, data=data,
        dedupe_key=dedupe_key, email_status="pending" if type_ in EMAIL_TYPES else "skipped",
    ).on_conflict_do_nothing(constraint="uq_notifications_dedupe")
    await session.execute(stmt)
