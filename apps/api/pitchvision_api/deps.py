"""Shared FastAPI dependencies (typed aliases for routers)."""
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from pitchvision_api.auth import Principal, Role, current_principal, require_role
from pitchvision_api.db import org_session
from pitchvision_api.settings import Settings


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]
EditorPrincipal = Annotated[Principal, Depends(require_role(Role.EDITOR))]
SettingsDep = Annotated[Settings, Depends(get_settings)]


async def _db_session(request: Request, principal: CurrentPrincipal) -> AsyncIterator[AsyncSession]:
    async with org_session(request.app.state.sessionmaker, principal.org_id) as session:
        yield session


# scope="function": commit/rollback happens when the endpoint returns, i.e. BEFORE the response is sent,
# so a client never sees 2xx for a transaction that later failed to commit.
DbSession = Annotated[AsyncSession, Depends(_db_session, scope="function")]
