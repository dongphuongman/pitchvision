"""Plans and their limits (UIUX SCR-41 / G.E06, AD §10.1 quota). identity-svc owns an org's plan (``subscriptions``)
and puts it in the access token's ``plan`` claim; the API enforces the limits below.

Prices and the Team allowance are still open (UIUX UX-5): Team = Pro's limits unless ``subscriptions.minutes_per_month``
overrides them per org.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class Plan(StrEnum):
    free = "free"
    pro = "pro"
    team = "team"


class PlanLimits(BaseModel):
    model_config = ConfigDict(frozen=True)

    minutes_per_month: int          # analysed video minutes per calendar month (UTC)
    export_max_height: int          # SCR-25 E09: 720p on Free, 1080p on Pro+
    export_watermark: bool          # PRD US-05: Free exports carry a watermark
    export_ttl_days: int = 7        # SCR-26 "Expires": the file is deleted after this


PLAN_LIMITS: dict[Plan, PlanLimits] = {
    Plan.free: PlanLimits(minutes_per_month=120, export_max_height=720, export_watermark=True),
    Plan.pro: PlanLimits(minutes_per_month=900, export_max_height=1080, export_watermark=False),
    Plan.team: PlanLimits(minutes_per_month=900, export_max_height=1080, export_watermark=False),
}


def plan_of(value: object) -> Plan:
    """A token's ``plan`` claim → Plan; anything missing / unknown is Free (never grants more than asked)."""
    try:
        return Plan(str(value))
    except ValueError:
        return Plan.free


def limits_for(plan: Plan, minutes_override: int | None = None) -> PlanLimits:
    base = PLAN_LIMITS[plan]
    return base if minutes_override is None else base.model_copy(update={"minutes_per_month": minutes_override})
