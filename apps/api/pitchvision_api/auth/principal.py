"""Who is calling: the verified identity + active org, and role checks (viewer < editor < admin < owner)."""

import uuid
from dataclasses import dataclass
from enum import StrEnum

from pitchvision_schemas import Plan, PlanLimits, limits_for


class Role(StrEnum):
    VIEWER = "viewer"
    EDITOR = "editor"
    ADMIN = "admin"
    OWNER = "owner"

    @property
    def rank(self) -> int:
        return _RANK[self]


_RANK = {Role.VIEWER: 0, Role.EDITOR: 1, Role.ADMIN: 2, Role.OWNER: 3}


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    org_id: uuid.UUID
    role: Role
    region: str
    plan: Plan = Plan.free
    quota_min: int | None = None  # the org's monthly minutes when identity-svc resolved an override

    @property
    def limits(self) -> PlanLimits:
        return limits_for(self.plan, self.quota_min)

    def has_role(self, minimum: Role | str) -> bool:
        return self.role.rank >= Role(minimum).rank
