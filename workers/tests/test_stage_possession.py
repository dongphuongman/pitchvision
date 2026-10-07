"""S9 possession / passes (M3-CV-02): synthetic ball + players → spells, passes, lost balls, team possession."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from cv_core.io.parquet import CLS_BALL, CLS_PLAYER, write_tracks
from pitchvision_schemas import Shot, ShotTimeline, ShotType
from pitchvision_worker.stages.possession import clean_ball, run_possession

FPS = 25


Pts = list[tuple[int, float, float]]


def _write(path: Path, ball: Pts, people: dict[int, tuple[int, Pts]]) -> Path:
    rows: list[tuple[int, int, int, int, float, float]] = [(f, -1, CLS_BALL, -1, x, y) for f, x, y in ball]
    for tid, (team, pts) in people.items():
        rows += [(f, tid, CLS_PLAYER, team, x, y) for f, x, y in pts]
    a = np.array(rows, dtype=float)
    n = len(a)
    cols = {"frame_idx": a[:, 0].astype(np.int32), "ts_ms": (a[:, 0] * 40).astype(np.int32),
            "track_id": a[:, 1].astype(np.int32), "cls": a[:, 2].astype(np.int8), "team": a[:, 3].astype(np.int8),
            "x1": np.zeros(n), "y1": np.zeros(n), "x2": np.zeros(n), "y2": np.zeros(n), "conf": np.ones(n),
            "px": a[:, 4], "py": a[:, 5], "h_quality": np.full(n, 0.9), "interpolated": np.zeros(n, bool)}  # fmt: skip
    return write_tracks(path, cols, {"stage": "S5"})


def _still(x: float, y: float, f0: int, f1: int) -> list[tuple[int, float, float]]:
    return [(f, x, y) for f in range(f0, f1)]


def _ball_path(*legs: tuple[int, int, float, float, float, float]) -> list[tuple[int, float, float]]:
    out = []
    for f0, f1, x0, y0, x1, y1 in legs:
        for f in range(f0, f1):
            u = (f - f0) / max(1, f1 - f0)
            out.append((f, x0 + (x1 - x0) * u, y0 + (y1 - y0) * u))
    return out


def test_pass_then_interception(tmp_path: Path) -> None:
    """A1 holds 1 s → passes 15 m to A2 (0.6 s flight) → A2 holds 1 s → B1 intercepts and keeps it 1 s."""
    people = {1: (0, _still(20, 30, 0, 150)), 2: (0, _still(35, 30, 0, 150)), 9: (1, _still(50, 30, 0, 150))}
    ball = _ball_path((0, 25, 20.3, 30, 20.3, 30), (25, 40, 20.3, 30, 34.7, 30), (40, 65, 34.7, 30, 34.7, 30),
                      (65, 80, 34.7, 30, 49.7, 30), (80, 105, 49.7, 30, 49.7, 30))  # fmt: skip
    r = run_possession(_write(tmp_path / "t.parquet", ball, people), None, FPS)
    assert [(s.track_id, s.team) for s in r.spells] == [(1, 0), (2, 0), (9, 1)]
    assert [(p.from_track, p.to_track, p.completed) for p in r.passes] == [(1, 2, True), (2, 9, False)]
    a, b = r.team[0], r.team[1]
    assert (a["passes"], a["passes_completed"], a["balls_lost"]) == (2, 1, 1)
    # A owns frames 0 → A2 losing the ball (its spells + the completed pass's flight; contact = within 2.5 m);
    # the interception's flight is nobody's; B owns from the ball reaching it
    assert a["possession_ms"] == pytest.approx(2700, abs=120) and b["possession_ms"] == pytest.approx(1050, abs=120)
    assert a["possession_pct"] == round(100 * a["possession_ms"] / (a["possession_ms"] + b["possession_ms"]), 1)
    assert a["thirds_ms"]["left"] == a["possession_ms"] and b["thirds_ms"]["middle"] == b["possession_ms"]
    first = a["pass_list"][0]
    assert (first["from"], first["to"], first["completed"]) == (1, 2, True)
    assert 900 <= first["t_ms"] < first["end_ms"] <= 1600 and first["x1"] - first["x0"] >= 10
    assert r.track[1]["passes_completed"] == 1 and r.track[2]["received"] == 1 and r.track[9]["touches"] == 1


def test_a_cut_breaks_the_chain(tmp_path: Path) -> None:
    people = {1: (0, _still(20, 30, 0, 100)), 2: (0, _still(35, 30, 0, 100))}
    ball = _ball_path((0, 25, 20, 30, 20, 30), (25, 40, 20, 30, 35, 30), (40, 65, 35, 30, 35, 30))
    path = _write(tmp_path / "t.parquet", ball, people)
    cut = ShotTimeline(shots=[Shot(t0_ms=0, t1_ms=1200, type=ShotType.main_wide, conf=1),
                              Shot(t0_ms=1200, t1_ms=4000, type=ShotType.main_wide, conf=1)])  # fmt: skip
    assert len(run_possession(path, None, FPS).passes) == 1
    assert run_possession(path, cut, FPS).passes == []


def test_a_short_duel_is_not_two_turnovers(tmp_path: Path) -> None:
    """A1 holds, B1 pokes at it for 0.5 s, A1 keeps going: no lost ball."""
    people = {1: (0, _still(20, 30, 0, 120)), 9: (1, _still(23.5, 30, 0, 120))}
    ball = _ball_path((0, 30, 20.2, 30, 20.2, 30), (30, 45, 23.4, 30, 23.4, 30), (45, 80, 20.2, 30, 20.2, 30))
    r = run_possession(_write(tmp_path / "t.parquet", ball, people), None, FPS)
    assert r.passes == [] and [s.team for s in r.spells] == [0, 0]


def test_ball_glitches_are_dropped() -> None:
    f = np.arange(40)
    x = np.full(40, 30.0)
    x[[10, 25]] = 80.0  # one-frame jumps to a white boot 50 m away
    keep = clean_ball(f, x, np.full(40, 20.0), FPS)
    assert not keep[10] and not keep[25] and keep[[0, 9, 11, 24, 26, 39]].all()
