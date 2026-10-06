"""exports persistence (runs in the caller's org-scoped transaction; queries still filter on org_id)."""

import uuid
from datetime import datetime
from typing import Literal

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from pitchvision_api.db.models import Export, Video
from pitchvision_api.repositories.videos import decode_cursor as _decode
from pitchvision_api.repositories.videos import encode_cursor as _encode

ACTIVE = ("queued", "running")
StatusFilter = Literal["all", "active", "done"]


def encode_cursor(e: Export) -> str:
    return _encode(e)  # type: ignore[arg-type]  # same (created_at, id) shape as videos


decode_cursor = _decode


async def add(session: AsyncSession, row: Export) -> Export:
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


async def get(session: AsyncSession, org_id: uuid.UUID, export_id: uuid.UUID, *, for_update: bool = False
              ) -> tuple[Export, str | None] | None:  # fmt: skip
    """(export, video title) — hidden once deleted or when its video was deleted."""
    stmt = (select(Export, Video.title).join(Video, Video.id == Export.video_id)
            .where(Export.org_id == org_id, Export.id == export_id, Export.deleted_at.is_(None),
                   Video.status != "deleted"))  # fmt: skip
    if for_update:
        stmt = stmt.with_for_update(of=Export)
    row = (await session.execute(stmt)).first()
    return (row[0], row[1]) if row else None


Row = tuple[Export, str | None]


async def page(session: AsyncSession, org_id: uuid.UUID, status: StatusFilter, limit: int,
               cursor: tuple[datetime, uuid.UUID] | None) -> tuple[list[Row], str | None]:  # fmt: skip
    """Newest first, keyset on (created_at, id) — ix_exports_org_created."""
    stmt = (select(Export, Video.title).join(Video, Video.id == Export.video_id)
            .where(Export.org_id == org_id, Export.deleted_at.is_(None), Video.status != "deleted"))  # fmt: skip
    if status == "active":
        stmt = stmt.where(Export.status.in_(ACTIVE))
    elif status == "done":
        stmt = stmt.where(Export.status == "done")
    if cursor is not None:
        stmt = stmt.where(tuple_(Export.created_at, Export.id) < tuple_(*cursor))
    stmt = stmt.order_by(Export.created_at.desc(), Export.id.desc()).limit(limit + 1)
    rows = [(r[0], r[1]) for r in (await session.execute(stmt)).all()]
    more = len(rows) > limit
    rows = rows[:limit]
    return rows, (encode_cursor(rows[-1][0]) if more and rows else None)
