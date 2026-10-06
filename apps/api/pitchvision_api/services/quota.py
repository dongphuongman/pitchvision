"""Monthly analysis minutes (M1-BE-18, AD §8.1 / §10.1, UIUX G.E06 / SCR-41 reason=quota).

Usage is derived, not stored: the minutes of every ``analyze`` job (derived jobs — team re-runs, recalibrations —
are free) created this calendar month (UTC) that has not failed or been cancelled, each charged
``⌈video duration / 1 min⌉``. A failed job costs nothing until it is retried (retry checks the quota again). A
YouTube video whose duration is not known yet counts 0 until the download finishes.

Checks (402 QUOTA_EXCEEDED, ``reason: quota``): starting an upload or a YouTube import needs minutes left; starting
(or retrying) a job needs ``used + this video's minutes ≤ limit``. A per-org advisory lock serialises the check with
the insert so two concurrent jobs cannot both squeeze under the limit.
"""

import math
from datetime import UTC, datetime

from pitchvision_schemas import ErrorCode
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pitchvision_api.auth import Principal
from pitchvision_api.errors import ApiError
from pitchvision_api.services import notifications

USED_SQL = text("""
SELECT coalesce(sum(ceil(coalesce(v.duration_ms, 0) / 60000.0)), 0)::int
  FROM jobs j JOIN videos v ON v.id = j.video_id AND v.org_id = j.org_id
 WHERE j.org_id = :org AND j.created_at >= :start AND j.created_at < :end
   AND j.status NOT IN ('failed', 'cancelled')
   AND coalesce(j.detail->>'kind', 'analyze') = 'analyze'
""")


class Usage(BaseModel):
    plan: str
    minutes_limit: int
    minutes_used: int
    period_start: datetime
    resets_at: datetime
    export_max_height: int
    export_watermark: bool


def period(now: datetime | None = None) -> tuple[datetime, datetime]:
    now = now or datetime.now(UTC)
    start = datetime(now.year, now.month, 1, tzinfo=UTC)
    end = datetime(now.year + (now.month == 12), now.month % 12 + 1, 1, tzinfo=UTC)
    return start, end


def minutes_of(duration_ms: int | None) -> int:
    return math.ceil((duration_ms or 0) / 60_000)


async def used_minutes(session: AsyncSession, principal: Principal, now: datetime | None = None) -> int:
    start, end = period(now)
    used = await session.scalar(USED_SQL, {"org": principal.org_id, "start": start, "end": end})
    return int(used or 0)


async def usage(session: AsyncSession, principal: Principal) -> Usage:
    start, end = period()
    lim = principal.limits
    return Usage(plan=principal.plan.value, minutes_limit=lim.minutes_per_month,
                 minutes_used=await used_minutes(session, principal), period_start=start, resets_at=end,
                 export_max_height=lim.export_max_height, export_watermark=lim.export_watermark)  # fmt: skip


async def ensure(session: AsyncSession, principal: Principal, needed: int = 0) -> None:
    """402 unless ``needed`` more minutes fit this month (``needed = 0``: at least one minute left). When the
    minutes are about to be used (``needed > 0``) and that crosses 80 % / 100 % of the limit, the caller gets the
    SCR-51 quota notification (once per month and mark)."""
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                          {"k": f"quota:{principal.org_id}"})  # fmt: skip
    used = await used_minutes(session, principal)
    limit = principal.limits.minutes_per_month
    if used + needed <= limit and used < limit:
        if needed:
            await _thresholds(session, principal, used, used + needed, limit)
        return
    _, resets = period()
    plan = principal.plan.value.capitalize()
    tail = f", video is {needed} minutes." if needed else "."
    raise ApiError(ErrorCode.QUOTA_EXCEEDED, 402, "Monthly processing quota exceeded",
                   f"{plan} plan allows {limit} minutes/month; {used} used{tail}",
                   extensions={"reason": "quota", "plan": principal.plan.value, "used": used, "limit": limit,
                               "needed": needed, "resets_at": resets.isoformat()})  # fmt: skip


async def _thresholds(session: AsyncSession, principal: Principal, before: int, after: int, limit: int) -> None:
    start, end = period()
    for pct, type_ in ((100, "quota_100"), (80, "quota_80")):
        mark = math.ceil(limit * pct / 100)
        if before < mark <= after:
            await notifications.add(session, principal, type_, {"used": after, "limit": limit, "plan":
                                    principal.plan.value, "resets_at": end.isoformat()},
                                    f"{type_}:{start:%Y-%m}")  # fmt: skip
            return
