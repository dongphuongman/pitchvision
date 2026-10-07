"""M3 stats API: per-team physical stats summed from S9 rows with the CURRENT team / role, and heatmaps from
positions_org — real Postgres (RLS)."""

import json
import uuid
from collections.abc import AsyncIterator

import httpx
import psycopg
import pytest
from pitchvision_api.main import create_app
from pitchvision_api.settings import Settings

from .conftest import Db
from .test_app import assert_problem
from .test_ingest_api import who
from .test_results_api import _video_job


@pytest.fixture
async def client(migrated: Db) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(Settings(env="local", dev_auth=True, database_url=migrated.app_url))
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await app.state.engine.dispose()


def _metrics(obs: float, dist: float, top: float | None, at: int | None, sprints: int = 0,
             avg: tuple[float, float] = (50, 30)) -> str:  # fmt: skip
    return json.dumps({"observed_s": obs, "distance_m": dist, "zones_m": {"walk": dist * 0.2, "jog": dist * 0.3,
                       "run": dist * 0.4, "sprint": dist * 0.1}, "top_speed_kmh": top, "top_speed_ms": at,
                       "sprints": sprints, "avg_x": avg[0], "avg_y": avg[1], "cov": [4.0, 1.0, 0.5],
                       "version": "pv-stats/v1"})  # fmt: skip


def _seed(db: Db, org: uuid.UUID, vid: uuid.UUID) -> None:
    """Team A: tracks 1 (player, 120 s, 300 m) + 2 (goalkeeper); team B: track 3; track 4 = referee (excluded);
    track 5 has no team. Shots: 60 s main_wide + 20 s close-up of a 100 s video."""
    with psycopg.connect(db.conninfo()) as c:  # owner stands in for the worker
        c.execute("UPDATE videos SET duration_ms = 100000, revision = 2 WHERE id = %s", (vid,))
        ta, tb = uuid.uuid4(), uuid.uuid4()
        for tid, cl, name in ((ta, 0, "Reds"), (tb, 1, "Blues")):
            c.execute("INSERT INTO teams (id, video_id, org_id, cluster, name, color) VALUES (%s,%s,%s,%s,%s,%s)",
                      (tid, vid, org, cl, name, "#ff0000" if cl == 0 else "#0000ff"))
        c.execute("INSERT INTO shots (video_id, org_id, t0_ms, t1_ms, type, conf) VALUES (%s,%s,0,60000,'main_wide',1),"
                  " (%s,%s,60000,80000,'close_up',1)", (vid, org, vid, org))  # fmt: skip
        players = [(1, ta, "player", _metrics(120, 300, 31.0, 4000, 2, (40, 30))),
                   (2, ta, "goalkeeper", _metrics(60, 60, 18.0, 9000, 0, (5, 34))),
                   (3, tb, "player", _metrics(90, 270, 33.5, 7000, 3, (70, 20))),
                   (4, None, "referee", _metrics(100, 500, 36.0, 1000)),
                   (5, None, "player", _metrics(4, 10, 20.0, 2000))]  # fmt: skip
        for track, team, role, m in players:
            pid = uuid.uuid4()
            c.execute("INSERT INTO players (id, video_id, org_id, track_id, team_id, role) VALUES (%s,%s,%s,%s,%s,%s)",
                      (pid, vid, org, track, team, role))
            c.execute("INSERT INTO stats_player (video_id, org_id, player_id, revision, metrics) "
                      "VALUES (%s,%s,%s,2,%s)", (vid, org, pid, m))  # fmt: skip
        # positions: track 1 at (10, 10) for 1 s, track 3 at (60, 40) for 0.4 s, referee + ball + a weak H ignored
        for ts in range(0, 1000, 200):
            c.execute("INSERT INTO positions (video_id, org_id, ts_ms, track_id, team, px, py, q) VALUES "
                      "(%s,%s,%s,1,0,10.2,10.4,0.9), (%s,%s,%s,4,NULL,30,30,0.9), (%s,%s,%s,-1,NULL,50,30,0.9), "
                      "(%s,%s,%s,2,0,20,20,0.2)", (vid, org, ts) * 4)  # fmt: skip
        for ts in (0, 200):
            c.execute("INSERT INTO positions (video_id, org_id, ts_ms, track_id, team, px, py, q) VALUES "
                      "(%s,%s,%s,3,1,60.1,40.1,0.8)", (vid, org, ts))  # fmt: skip


async def test_team_totals_use_current_roles_and_label_estimates(client: httpx.AsyncClient, migrated: Db) -> None:
    org = uuid.uuid4()
    vid, _ = await _video_job(migrated, org)
    _seed(migrated, org, vid)
    r = await client.get(f"/api/v1/results/{vid}/stats", headers=who(org, "viewer"))
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["available"] and s["revision"] == 2 and s["main_wide_ms"] == 60000 and s["duration_ms"] == 100000
    a, b = s["teams"]
    assert (a["cluster"], a["name"], a["tracks"], a["distance_m"], a["observed_player_s"]) == (0, "Reds", 2, 360, 180)
    assert a["sprints"] == 2 and a["zones_m"]["run"] == pytest.approx(144)
    assert a["top_speed"] == {"kmh": 31.0, "at_ms": 4000, "track_id": 1, "cluster": 0}
    assert a["m_per_min"] == 120.0  # 360 m over 3 observed player-minutes
    assert a["estimated_distance_m"] == pytest.approx(120 * 11 * 100 / 60, abs=1)
    assert b["distance_m"] == 270 and b["m_per_min"] == 180.0
    # the referee (36 km/h) is not a team's player and not in the top speeds
    assert [t["track_id"] for t in s["top_speeds"]] == [3, 1, 2]
    # players: observed ≥ 10 s by default (track 5 = 4 s left out), longest first, with average position + spread
    assert [p["track_id"] for p in s["players"]] == [1, 4, 3, 2]
    assert s["players"][0]["avg_x"] == 40 and s["players"][0]["cov"] == [4.0, 1.0, 0.5]

    # SCR-14 E06 "this goalkeeper is a referee": totals follow without any re-run
    with psycopg.connect(migrated.conninfo()) as c:
        c.execute("UPDATE players SET role = 'referee', team_id = NULL WHERE video_id = %s AND track_id = 2", (vid,))
    a2 = (await client.get(f"/api/v1/results/{vid}/stats", headers=who(org))).json()["teams"][0]
    assert a2["tracks"] == 1 and a2["distance_m"] == 300

    other = await client.get(f"/api/v1/results/{vid}/stats", headers=who(uuid.uuid4()))
    assert_problem(other, 404, "NOT_FOUND")


async def test_stats_unavailable_for_an_old_analysis(client: httpx.AsyncClient, migrated: Db) -> None:
    org = uuid.uuid4()
    vid, _ = await _video_job(migrated, org)
    s = (await client.get(f"/api/v1/results/{vid}/stats", headers=who(org))).json()
    assert s["available"] is False and s["teams"] == [] and s["players"] == []


async def test_heatmap_bins_by_team_track_and_time(client: httpx.AsyncClient, migrated: Db) -> None:
    org = uuid.uuid4()
    vid, _ = await _video_job(migrated, org)
    _seed(migrated, org, vid)
    url = f"/api/v1/results/{vid}/heatmap"
    h = (await client.get(url, headers=who(org))).json()
    assert (h["cols"], h["rows"], h["cell_m"], h["sample_s"]) == (42, 28, 2.5, 0.2)
    # track 1 (5 samples at 10.2, 10.4 → cell 4, 4) + track 3 (2 samples → cell 24, 16); referee, ball, weak H out
    assert h["total"] == 7 and h["counts"][4 * 42 + 4] == 5 and h["counts"][16 * 42 + 24] == 2
    assert (await client.get(url, params={"cluster": 1}, headers=who(org))).json()["total"] == 2
    assert (await client.get(url, params={"track_id": 1, "to_ms": 400}, headers=who(org))).json()["total"] == 2
    coarse = (await client.get(url, params={"cell_m": 5}, headers=who(org))).json()
    assert (coarse["cols"], coarse["rows"]) == (21, 14) and coarse["total"] == 7
    bad = await client.get(url, params={"from_ms": 500, "to_ms": 400}, headers=who(org))
    assert_problem(bad, 422, "VALIDATION_FAILED")
    assert (await client.get(url, headers=who(uuid.uuid4()))).status_code == 404
