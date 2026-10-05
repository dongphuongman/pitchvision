"""Async engine + tenant-scoped sessions. Every transaction runs with `app.org_id` set, so RLS applies."""
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


def make_engine(url: str, **kwargs: Any) -> AsyncEngine:
    return create_async_engine(url, pool_pre_ping=True, **kwargs)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def set_org(session: AsyncSession, org_id: uuid.UUID) -> None:
    """SET LOCAL app.org_id for the current transaction (set_config(..., true) is the bind-param form)."""
    await session.execute(text("SELECT set_config('app.org_id', :org, true)"), {"org": str(org_id)})


@asynccontextmanager
async def org_session(sessionmaker: async_sessionmaker[AsyncSession], org_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    """One transaction scoped to org_id: commits on success, rolls back on error."""
    async with sessionmaker() as session, session.begin():
        await set_org(session, org_id)
        yield session
