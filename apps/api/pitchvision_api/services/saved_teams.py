"""Saved teams (SCR-40 O03, SCR-14 E02): the org's team names + display colours.

Confirming teams (SCR-14 E09) upserts every non-default name (``remember``); suggestions come most-used first. Admins
rename / recolour / delete entries; the videos already analysed keep their own names (the ``teams`` table).
"""

import uuid
from datetime import UTC, datetime

from pitchvision_schemas import ErrorCode
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from pitchvision_api.auth import Principal
from pitchvision_api.db.models import SavedTeam
from pitchvision_api.errors import ApiError
from pitchvision_api.schemas.saved_teams import SavedTeamOut, SavedTeamPatch

DEFAULT_NAMES = frozenset({"team a", "team b"})  # what S8 names the clusters (stages/load_db.TEAM_NAMES)
LIMIT = 200


async def remember(session: AsyncSession, org_id: uuid.UUID, teams: list[tuple[str, str]]) -> None:
    """Upsert (name, colour) pairs; the latest colour wins. Default names are skipped."""
    now = datetime.now(UTC)
    for name, color in teams:
        if name.strip().lower() in DEFAULT_NAMES:
            continue
        stmt = pg_insert(SavedTeam).values(org_id=org_id, name=name, color=color.lower(), last_used_at=now)
        stmt = stmt.on_conflict_do_update(
            index_elements=[SavedTeam.org_id, func.lower(SavedTeam.name)],
            set_={"color": stmt.excluded.color, "uses": SavedTeam.uses + 1, "last_used_at": now})
        await session.execute(stmt)


async def page(session: AsyncSession, principal: Principal) -> list[SavedTeamOut]:
    order = (SavedTeam.uses.desc(), SavedTeam.last_used_at.desc())
    rows = await session.scalars(select(SavedTeam).where(SavedTeam.org_id == principal.org_id)
                                 .order_by(*order).limit(LIMIT))  # fmt: skip
    return [SavedTeamOut.of(t) for t in rows]


async def _get(session: AsyncSession, principal: Principal, team_id: uuid.UUID) -> SavedTeam:
    t = await session.scalar(select(SavedTeam).where(SavedTeam.org_id == principal.org_id, SavedTeam.id == team_id)
                             .with_for_update())  # fmt: skip
    if t is None:
        raise ApiError(ErrorCode.NOT_FOUND, 404, "Saved team not found")
    return t


async def patch(session: AsyncSession, principal: Principal, team_id: uuid.UUID, body: SavedTeamPatch
                ) -> SavedTeamOut:  # fmt: skip
    t = await _get(session, principal, team_id)
    if body.name is not None and body.name.lower() != t.name.lower():
        clash = await session.scalar(select(SavedTeam.id).where(
            SavedTeam.org_id == principal.org_id, func.lower(SavedTeam.name) == body.name.lower()))  # fmt: skip
        if clash is not None:
            raise ApiError(ErrorCode.CONFLICT, 409, "Name already saved", f"“{body.name}” is already a saved team.",
                           extensions={"reason": "name_taken"})  # fmt: skip
    if body.name is not None:
        t.name = body.name
    if body.color is not None:
        t.color = body.color.lower()
    await session.flush()
    return SavedTeamOut.of(t)


async def delete(session: AsyncSession, principal: Principal, team_id: uuid.UUID) -> None:
    await session.delete(await _get(session, principal, team_id))
    await session.flush()
