"""idempotency_keys persistence. Runs inside the caller's org-scoped transaction (RLS applies)."""
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from pitchvision_api.db.models import IdempotencyKey

TTL = timedelta(hours=24)


async def claim(
    session: AsyncSession, org_id: uuid.UUID, key: str, method: str, path: str, request_hash: str
) -> IdempotencyKey | None:
    """Reserve `key`; None if we now own it, else the live row someone else stored.

    A concurrent claim of the same key blocks on the unique index until the owner's transaction ends,
    so the loser sees either the finished response (replay) or nothing (owner rolled back → it claims).
    Rows past TTL are taken over in place.
    """
    t = IdempotencyKey
    stmt = (
        insert(t)
        .values(org_id=org_id, key=key, method=method, path=path, request_hash=request_hash)
        .on_conflict_do_update(
            index_elements=[t.org_id, t.key],
            set_={
                "method": method,
                "path": path,
                "request_hash": request_hash,
                "response_status": None,
                "response_body": None,
                "created_at": func.now(),
            },
            where=t.created_at < func.now() - TTL,
        )
        .returning(t.key)
    )
    if (await session.execute(stmt)).scalar_one_or_none() is not None:
        return None
    existing = select(t).where(t.org_id == org_id, t.key == key).execution_options(populate_existing=True)
    return await session.scalar(existing)


async def save_response(session: AsyncSession, org_id: uuid.UUID, key: str, status: int, body: Any) -> None:
    t = IdempotencyKey
    await session.execute(
        update(t).where(t.org_id == org_id, t.key == key).values(response_status=status, response_body=body)
    )


async def release(session: AsyncSession, org_id: uuid.UUID, key: str) -> None:
    t = IdempotencyKey
    await session.execute(delete(t).where(t.org_id == org_id, t.key == key))
