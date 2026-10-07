"""/api/v1/saved-teams — SCR-40 O03 (admin+ edit) and SCR-14 E02 name suggestions (any member reads)."""

import uuid

from fastapi import APIRouter, Response

from pitchvision_api.deps import AdminPrincipal, CurrentPrincipal, DbSession
from pitchvision_api.schemas.saved_teams import SavedTeamOut, SavedTeamPatch
from pitchvision_api.services import saved_teams as service

router = APIRouter(prefix="/saved-teams", tags=["teams"])


@router.get("")
async def list_saved_teams(principal: CurrentPrincipal, session: DbSession) -> list[SavedTeamOut]:
    """The org's team names + colours, most used first (≤ 200). Saved when teams are confirmed (SCR-14)."""
    return await service.page(session, principal)


@router.patch("/{team_id}")
async def patch_saved_team(team_id: uuid.UUID, body: SavedTeamPatch, principal: AdminPrincipal, session: DbSession
                           ) -> SavedTeamOut:  # fmt: skip
    """Rename / recolour (admin+). 409 `name_taken`. Videos already analysed keep their names."""
    return await service.patch(session, principal, team_id, body)


@router.delete("/{team_id}", status_code=204)
async def delete_saved_team(team_id: uuid.UUID, principal: AdminPrincipal, session: DbSession) -> Response:
    await service.delete(session, principal, team_id)
    return Response(status_code=204)
