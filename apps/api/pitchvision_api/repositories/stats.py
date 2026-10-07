"""Stats reads (M3): per-track S9 metrics joined with the CURRENT player team / role, and heatmap bins."""

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

MIN_Q = 0.5  # positions with a usable homography only (S9's bar)


async def player_metrics(session: AsyncSession, org_id: uuid.UUID, video_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = await session.execute(text("""
        SELECT p.track_id, p.role, t.cluster, s.metrics, s.revision
          FROM stats_player s
          JOIN players p ON p.id = s.player_id
          LEFT JOIN teams t ON t.id = p.team_id
         WHERE s.org_id = :o AND s.video_id = :v
    """), {"o": org_id, "v": video_id})  # fmt: skip
    return [dict(r) for r in rows.mappings()]


async def heatmap_bins(session: AsyncSession, video_id: uuid.UUID, cell_m: float, *, cluster: int | None,
                       track_id: int | None, from_ms: int | None, to_ms: int | None,
                       length_m: float, width_m: float) -> list[tuple[int, int, int]]:  # fmt: skip
    """[(col, row, count)] of people (no ball, no referees) inside the pitch, team / track / time filtered."""
    where = ["p.video_id = :v", "p.track_id >= 0", "p.px BETWEEN 0 AND :L", "p.py BETWEEN 0 AND :W", "p.q >= :q",
             "coalesce(pl.role, 'player') <> 'referee'"]  # fmt: skip
    params: dict[str, Any] = {"v": video_id, "c": cell_m, "L": length_m, "W": width_m, "q": MIN_Q}
    if cluster is not None:
        where.append("t.cluster = :cl")
        params["cl"] = cluster
    if track_id is not None:
        where.append("p.track_id = :tid")
        params["tid"] = track_id
    if from_ms is not None:
        where.append("p.ts_ms >= :t0")
        params["t0"] = from_ms
    if to_ms is not None:
        where.append("p.ts_ms < :t1")
        params["t1"] = to_ms
    sql = f"""
        SELECT least(floor(p.px / :c)::int, ceil(:L / :c)::int - 1) AS i,
               least(floor(p.py / :c)::int, ceil(:W / :c)::int - 1) AS j, count(*) AS n
          FROM positions_org p
          LEFT JOIN players pl ON pl.video_id = p.video_id AND pl.track_id = p.track_id
          LEFT JOIN teams t ON t.id = pl.team_id
         WHERE {" AND ".join(where)}
         GROUP BY 1, 2"""
    return [(int(i), int(j), int(n)) for i, j, n in (await session.execute(text(sql), params)).all()]
