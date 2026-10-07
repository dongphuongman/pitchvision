"""Match stats from positions (M3-CV-01 physical, FR-503 heatmap, FR-504 average positions; SCR-20a / SCR-20c).

S9 (worker, ``pitchvision_worker.stages.stats``) stores one metrics row per track; teams are summed HERE with the
current ``players`` team / role, so SCR-14 E06 fixes (goalkeeper / referee / team) count without re-running
anything. Referees and tracks without a team are left out of team totals. Heatmaps bin the 5 Hz ``positions``
on demand (any team / track / time window), which also respects those fixes.
"""

import math
import uuid
from collections import defaultdict
from typing import Any

from pitchvision_schemas import ErrorCode

from pitchvision_api.auth import Principal
from pitchvision_api.errors import ApiError
from pitchvision_api.repositories import results as results_repo
from pitchvision_api.repositories import stats as repo
from pitchvision_api.repositories import videos as videos_repo
from pitchvision_api.schemas.stats import (
    HeatmapOut,
    PassList,
    PassOut,
    PlayerStats,
    PossessionMinute,
    StatsOut,
    TeamStats,
    ThirdsMs,
    TopSpeed,
    ZonesM,
)
from pitchvision_api.services.videos import not_found

TEAM_ROLES = ("player", "goalkeeper")
PLAYERS_ON_PITCH = 11
TOP_SPEEDS = 5
PITCH_L, PITCH_W = 105.0, 68.0
ZONES = ("walk", "jog", "run", "sprint")


def _r(v: float, nd: int = 1) -> float:
    return round(v, nd)


async def stats(session: Any, principal: Principal, video_id: uuid.UUID, min_observed_s: float) -> StatsOut:
    video = await videos_repo.get(session, principal.org_id, video_id)
    if video is None:
        raise not_found()
    rows = await repo.player_metrics(session, principal.org_id, video_id)
    shots = await results_repo.shots(session, principal.org_id, video_id)
    main_wide_ms = sum(s.t1_ms - s.t0_ms for s in shots if s.type == "main_wide")
    teams = {t.cluster: t for t in await results_repo.teams(session, principal.org_id, video_id) if t.cluster in (0, 1)}
    if not rows:
        return StatsOut(available=False, revision=video.revision, duration_ms=video.duration_ms,
                        main_wide_ms=main_wide_ms, teams=[], top_speeds=[], players=[])  # fmt: skip

    agg: dict[int, dict[str, Any]] = defaultdict(lambda: {"tracks": 0, "obs": 0.0, "dist": 0.0, "sprints": 0,
                                                         "zones": dict.fromkeys(ZONES, 0.0), "top": None})  # fmt: skip
    tops: list[TopSpeed] = []
    players: list[PlayerStats] = []
    for r in rows:
        m: dict[str, Any] = r["metrics"] or {}
        cluster = r["cluster"] if r["cluster"] in (0, 1) else None
        role = r["role"]
        kmh, at = m.get("top_speed_kmh"), m.get("top_speed_ms")
        top = TopSpeed(kmh=kmh, at_ms=at, track_id=r["track_id"], cluster=cluster) if kmh and at is not None else None
        if role in TEAM_ROLES and cluster is not None:
            a = agg[cluster]
            a["tracks"] += 1
            a["obs"] += float(m.get("observed_s") or 0)
            a["dist"] += float(m.get("distance_m") or 0)
            a["sprints"] += int(m.get("sprints") or 0)
            for z in ZONES:
                a["zones"][z] += float((m.get("zones_m") or {}).get(z) or 0)
            if top and (a["top"] is None or top.kmh > a["top"].kmh):
                a["top"] = top
            if top:
                tops.append(top)
        if float(m.get("observed_s") or 0) >= min_observed_s:
            players.append(PlayerStats(track_id=r["track_id"], cluster=cluster, role=role,
                                       observed_s=float(m.get("observed_s") or 0),
                                       distance_m=float(m.get("distance_m") or 0),
                                       top_speed_kmh=kmh, top_speed_ms=at, sprints=int(m.get("sprints") or 0),
                                       avg_x=m.get("avg_x"), avg_y=m.get("avg_y"), cov=m.get("cov")))  # fmt: skip

    poss = await repo.team_metrics(session, principal.org_id, video_id)
    minutes = (video.duration_ms or 0) / 60000
    out_teams: list[TeamStats] = []
    for cluster in sorted(set(agg) | set(teams)):
        a = agg[cluster]  # a team with no observed tracks gets zeros
        team = teams.get(cluster)
        rate = a["dist"] / (a["obs"] / 60) if a["obs"] >= 60 else None  # < 1 player-minute: too little to rate
        out_teams.append(TeamStats(
            cluster=cluster, name=team.name if team else None, color=team.color if team else None,
            tracks=a["tracks"], observed_player_s=_r(a["obs"]), distance_m=_r(a["dist"]),
            zones_m=ZonesM(**{z: _r(v) for z, v in a["zones"].items()}), sprints=a["sprints"], top_speed=a["top"],
            m_per_min=_r(rate) if rate is not None else None,
            estimated_distance_m=_r(rate * PLAYERS_ON_PITCH * minutes, 0) if rate is not None and minutes else None,
            **_possession(poss.get(cluster)),
        ))  # fmt: skip
    tops.sort(key=lambda t: -t.kmh)
    players.sort(key=lambda p: -p.observed_s)
    by_minute: dict[int, dict[int, int]] = defaultdict(dict)
    for cluster, m in poss.items():
        for minute, ms in (m.get("timeline_ms") or {}).items():
            by_minute[int(minute)][cluster] = int(ms)
    timeline = [PossessionMinute(minute=k, ms=v) for k, v in sorted(by_minute.items())]
    return StatsOut(available=True, revision=max(int(r["revision"]) for r in rows), duration_ms=video.duration_ms,
                    main_wide_ms=main_wide_ms, teams=out_teams, top_speeds=tops[:TOP_SPEEDS], players=players,
                    possession_timeline=timeline)  # fmt: skip


def _possession(m: dict[str, Any] | None) -> dict[str, Any]:
    if not m:
        return {}
    return {"possession_pct": m.get("possession_pct"), "possession_ms": m.get("possession_ms"),
            "thirds_ms": ThirdsMs(**(m.get("thirds_ms") or {})), "passes": m.get("passes"),
            "passes_completed": m.get("passes_completed"), "balls_lost": m.get("balls_lost")}  # fmt: skip


async def passes(session: Any, principal: Principal, video_id: uuid.UUID, cluster: int | None,
                 completed: bool | None) -> PassList:  # fmt: skip
    if await videos_repo.get(session, principal.org_id, video_id) is None:
        raise not_found()
    items = [PassOut(cluster=cl, from_track=p["from"], to_track=p["to"], t_ms=p["t_ms"], end_ms=p["end_ms"],
                     completed=p["completed"], x0=p["x0"], y0=p["y0"], x1=p["x1"], y1=p["y1"])
             for cl, m in (await repo.team_metrics(session, principal.org_id, video_id)).items()
             if cluster is None or cl == cluster
             for p in m.get("pass_list") or []
             if completed is None or p["completed"] == completed]  # fmt: skip
    return PassList(items=sorted(items, key=lambda p: p.t_ms))


async def heatmap(session: Any, principal: Principal, video_id: uuid.UUID, *, cell_m: float, cluster: int | None,
                  track_id: int | None, from_ms: int | None, to_ms: int | None) -> HeatmapOut:  # fmt: skip
    if from_ms is not None and to_ms is not None and to_ms <= from_ms:
        raise ApiError(ErrorCode.VALIDATION_FAILED, 422, "Invalid time window", "to_ms must be > from_ms.")
    if await videos_repo.get(session, principal.org_id, video_id) is None:
        raise not_found()
    cols, rows = math.ceil(PITCH_L / cell_m), math.ceil(PITCH_W / cell_m)
    counts = [0] * (cols * rows)
    total = 0
    bins = await repo.heatmap_bins(session, video_id, cell_m, cluster=cluster, track_id=track_id, from_ms=from_ms,
                                   to_ms=to_ms, length_m=PITCH_L, width_m=PITCH_W)  # fmt: skip
    for i, j, n in bins:
        if 0 <= i < cols and 0 <= j < rows:
            counts[j * cols + i] += n
            total += n
    return HeatmapOut(cell_m=cell_m, cols=cols, rows=rows, total=total, counts=counts)
