"""S8 load-db (M1-BE-14): 5 Hz positions + teams + players; DB writes as pv_worker, idempotent, tenant-guarded."""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from cv_core.io.parquet import CLS_BALL, CLS_GOALKEEPER, CLS_PLAYER, CLS_REFEREE, NO_TRACK, write_tracks
from pitchvision_worker.repository import org_tx
from pitchvision_worker.results_db import write_results
from pitchvision_worker.stages.load_db import DEFAULT_COLORS, guess_attack, prepare_load, team_rows
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import DBAPIError, ProgrammingError


def tracks_file(path: Path, *, frames: int = 50) -> Path:
    """Track 1 (team 0, 2.5 m/s), track 2 (team 1 goalkeeper, teleports 10 m/frame), track 3 (referee, no H), ball."""
    rows: dict[str, list[Any]] = {k: [] for k in ("frame_idx", "ts_ms", "track_id", "cls", "team", "x1", "y1", "x2",
                                                 "y2", "conf", "px", "py", "h_quality", "interpolated")}

    def add(f: int, tid: int, cls: int, team: int, px: float, py: float, hq: float) -> None:
        for k, v in zip(rows, (f, f * 40, tid, cls, team, 10, 10, 30, 60, 0.9, px, py, hq, False), strict=True):
            rows[k].append(v)

    for f in range(frames):
        add(f, NO_TRACK, CLS_BALL, -1, 50.0 + f, 30.0, 0.8)
        add(f, 1, CLS_PLAYER, 0, 10 + 0.1 * f, 20.0, 0.8)
        add(f, 2, CLS_GOALKEEPER, 1, 10.0 * (f % 2), 5.0, 0.8)
        add(f, 3, CLS_REFEREE, -1, float("nan"), float("nan"), float("nan"))
    return write_tracks(path, {k: np.asarray(v) for k, v in rows.items()})


def test_prepare_load_5hz_velocities_players_teams(tmp_path: Path) -> None:
    tm = {"stats": {"colors": {"0": "#C8102E", "1": "not-a-colour"}}}
    b = prepare_load(tracks_file(tmp_path / "t.parquet"), tm)
    p = b.positions
    assert set(p["ts_ms"].tolist()) == set(range(0, 2000, 200))  # every 5th frame of 25 fps
    assert len(p["ts_ms"]) == 10 * 4 and b.stats["step_frames"] == 5
    assert np.all(np.diff(p["ts_ms"]) >= 0)
    one = p["track_id"] == 1
    assert np.isnan(p["vx"][one][0]) and np.allclose(p["vx"][one][1:], 2.5) and np.allclose(p["vy"][one][1:], 0)
    assert np.isnan(p["vx"][p["track_id"] == 2]).all()  # 25 m/s for a person = outlier
    ball = p["track_id"] == NO_TRACK
    assert np.allclose(p["vx"][ball][1:], 25.0)  # the ball may be fast
    assert np.isnan(p["px"][p["track_id"] == 3]).all() and np.isnan(p["q"][p["track_id"] == 3]).all()
    assert [(pl.track_id, pl.cluster, pl.role) for pl in b.players] == [(1, 0, "player"), (2, 1, "goalkeeper"),
                                                                         (3, None, "referee")]  # fmt: skip
    assert [(pl.frames, pl.t0_ms, pl.t1_ms) for pl in b.players] == [(50, 0, 1960)] * 3
    teams = [(t.cluster, t.name, t.color) for t in b.teams]
    assert teams == [(0, "Team A", "#c8102e"), (1, "Team B", DEFAULT_COLORS[1])]
    assert [t.color for t in team_rows(None)] == [DEFAULT_COLORS[0], DEFAULT_COLORS[1]]
    # team 1's goalkeeper stands at x ≈ 5 m (its own goal on the left) → team 1 attacks right, team 0 left
    assert b.attack_h1 == "left" and b.stats["attack_h1"] == "left"


def _gk_cols(team: int, px: float, x: float, n: int = 60) -> dict[str, np.ndarray]:
    return {"ts_ms": np.arange(n) * 40, "cls": np.full(n, 1), "team": np.full(n, team),
            "px": np.full(n, px), "x1": np.full(n, x - 10), "x2": np.full(n, x + 10)}  # fmt: skip


def _cat(*parts: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    return {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}


def test_guess_attack() -> None:
    nan = float("nan")
    assert guess_attack(_gk_cols(0, 100.0, 0), 1280) == "left"            # team 0 keeper on the right (metres)
    assert guess_attack(_gk_cols(0, nan, 200), 1280) == "right"           # no homography: image x < 640
    assert guess_attack(_cat(_gk_cols(0, nan, 1100), _gk_cols(1, nan, 150)), 1280) == "left"   # both agree
    assert guess_attack(_cat(_gk_cols(0, nan, 1100), _gk_cols(1, nan, 1150)), 1280) is None    # disagree
    assert guess_attack(_gk_cols(0, 10.0, 0, n=10), 1280) is None         # too little evidence
    # only the first half counts: the keepers swap ends after the break
    late = _gk_cols(0, 100.0, 0)
    late["ts_ms"] = late["ts_ms"] + 10_000_000
    assert guess_attack(_cat(_gk_cols(0, 10.0, 0, n=30), late), 1280) == "right"


# ---------------------------------------------------------------- DB (Docker)
@pytest.fixture
def worker_db(pg_worker_url: str) -> Iterator[Engine]:
    e = create_engine(pg_worker_url)
    yield e
    e.dispose()


@pytest.fixture
def app_db(pg_app_url: str) -> Iterator[Engine]:
    e = create_engine(pg_app_url)
    yield e
    e.dispose()


def _video(engine: Engine, org: uuid.UUID) -> uuid.UUID:
    vid = uuid.uuid4()
    with org_tx(engine, org) as c:
        c.execute(text("INSERT INTO videos (id, org_id, created_by, source_type, status) "
                       "VALUES (:v, :o, :u, 'upload', 'ready')"), {"v": vid, "o": org, "u": uuid.uuid4()})  # fmt: skip
    return vid


def _q(engine: Engine, org: uuid.UUID, sql: str, **params: Any) -> list[Any]:
    with org_tx(engine, org) as c:
        return list(c.execute(text(sql), params))


def test_write_results_idempotent_and_rls(tmp_path: Path, worker_db: Engine, app_db: Engine) -> None:
    org, other = uuid.uuid4(), uuid.uuid4()
    vid = _video(worker_db, org)
    batch = prepare_load(tracks_file(tmp_path / "t.parquet"), {"stats": {"colors": {"0": "#112233"}}})
    first = write_results(worker_db, org, vid, batch)
    assert first == {"positions": 40, "positions_replaced": 0, "teams": 2, "players": 3, "players_removed": 0}
    again = write_results(worker_db, org, vid, batch)
    assert again["positions_replaced"] == 40 and again["positions"] == 40

    # the API (pv_app) reads positions only through positions_org, filtered on its org
    cnt = "SELECT count(*), count(px), count(team) FROM positions_org WHERE video_id = :v"
    assert _q(app_db, org, cnt, v=vid) == [(40, 30, 20)]  # referee has no px; ball / referee have no team
    assert _q(app_db, other, cnt, v=vid) == [(0, 0, 0)]
    teams = _q(app_db, org, "SELECT cluster, name, color, attack_h1, attack_source FROM teams WHERE video_id = :v "
               "ORDER BY cluster", v=vid)  # fmt: skip
    assert teams == [(0, "Team A", "#112233", "left", "auto"), (1, "Team B", DEFAULT_COLORS[1], "right", "auto")]
    assert _q(app_db, org, "SELECT frames, t0_ms, t1_ms FROM players WHERE video_id = :v AND track_id = 2",
              v=vid) == [(50, 0, 1960)]  # fmt: skip
    pl = _q(app_db, org, "SELECT p.track_id, p.role, t.cluster FROM players p LEFT JOIN teams t ON t.id = p.team_id "
            "WHERE p.video_id = :v ORDER BY p.track_id", v=vid)  # fmt: skip
    assert pl == [(1, "player", 0), (2, "goalkeeper", 1), (3, "referee", None)]
    assert _q(app_db, other, "SELECT count(*) FROM players WHERE video_id = :v", v=vid) == [(0,)]

    # user-confirmed rows survive a re-run; stale unconfirmed players go
    with org_tx(app_db, org) as c:
        c.execute(text("UPDATE teams SET name = 'Reds', confirmed = true WHERE video_id = :v AND cluster = 0"),
                  {"v": vid})
        c.execute(text("UPDATE teams SET attack_h1 = CASE cluster WHEN 0 THEN 'right' ELSE 'left' END, "
                       "attack_source = 'user' WHERE video_id = :v"), {"v": vid})  # fmt: skip
        c.execute(text("UPDATE players SET confirmed = true, role = 'player' WHERE video_id = :v AND track_id = 3"),
                  {"v": vid})
    fewer = prepare_load(tracks_file(tmp_path / "t.parquet"), None)
    fewer.players = [pl for pl in fewer.players if pl.track_id != 2]
    res = write_results(worker_db, org, vid, fewer)
    assert res["players_removed"] == 1
    assert _q(app_db, org, "SELECT name, color FROM teams WHERE video_id = :v AND cluster = 0", v=vid) == \
        [("Reds", "#112233")]
    assert _q(app_db, org, "SELECT attack_h1, attack_source FROM teams WHERE video_id = :v ORDER BY cluster",
              v=vid) == [("right", "user"), ("left", "user")]  # a re-run never overwrites the user's direction
    assert _q(app_db, org, "SELECT track_id, role FROM players WHERE video_id = :v ORDER BY track_id", v=vid) == \
        [(1, "player"), (3, "player")]


def test_positions_write_path_is_tenant_guarded(worker_db: Engine, app_db: Engine) -> None:
    org, other = uuid.uuid4(), uuid.uuid4()
    vid = uuid.uuid4()
    insert = "INSERT INTO positions (video_id, org_id, ts_ms, track_id) VALUES (:v, :o, 0, 1)"
    with pytest.raises(DBAPIError, match=r"does not match app\.org_id"), org_tx(worker_db, org) as c:
        c.execute(text(insert), {"v": vid, "o": other})
    with pytest.raises(DBAPIError, match=r"does not match app\.org_id"), worker_db.begin() as c:  # no org context
        c.execute(text(insert), {"v": vid, "o": org})
    with org_tx(worker_db, org) as c:
        c.execute(text(insert), {"v": vid, "o": org})
    # pv_worker: no direct SELECT / DELETE on the hypertable; deletes only through the org-scoped helper
    with pytest.raises(ProgrammingError, match="permission denied"), org_tx(worker_db, org) as c:
        c.execute(text("DELETE FROM positions WHERE video_id = :v"), {"v": vid})
    assert _q(worker_db, other, "SELECT pv_delete_positions(:v)", v=vid) == [(0,)]  # other org deletes nothing
    assert _q(worker_db, org, "SELECT pv_delete_positions(:v)", v=vid) == [(1,)]
    # the API role cannot write positions at all
    with pytest.raises(ProgrammingError, match="permission denied"), org_tx(app_db, org) as c:
        c.execute(text(insert), {"v": vid, "o": org})
    with pytest.raises(ProgrammingError, match="permission denied"), org_tx(app_db, org) as c:
        c.execute(text("SELECT pv_delete_positions(:v)"), {"v": vid})
    roles = _q(app_db, org, "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'pv_worker'")
    assert [tuple(r) for r in roles] == [("pv_worker", False, False)]
