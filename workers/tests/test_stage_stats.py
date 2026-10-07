"""S9 physical stats (M3-CV-01): synthetic trajectories → distance, zones, sprints, top speed, average position."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from cv_core.io.parquet import CLS_BALL, CLS_PLAYER, write_tracks
from pitchvision_worker.stages.stats import run_stats, track_stats

FPS = 25.0


def _path(speeds_ms: list[tuple[float, float]], noise_m: float = 0.0, seed: int = 0) -> tuple[np.ndarray, ...]:
    """[(seconds, m/s), …] along x from (20, 30) → frames, ts, px, py (+ Gaussian jitter like box-bottom noise)."""
    v = np.concatenate([np.full(int(s * FPS), sp) for s, sp in speeds_ms])
    x = 20 + np.r_[0, np.cumsum(v[:-1] / FPS)]
    rng = np.random.default_rng(seed)
    f = np.arange(len(v))
    return f, f * 40, x + rng.normal(0, noise_m, len(v)), 30 + rng.normal(0, noise_m, len(v))


def test_steady_run_distance_zone_and_no_sprint() -> None:
    s = track_stats(1, *_path([(10, 5.0)]), fps=FPS)  # 18 km/h for 10 s
    assert s.distance_m == pytest.approx(50, rel=0.03)
    assert s.zones_m["run"] == pytest.approx(s.distance_m, rel=0.02)
    assert s.sprints == 0 and s.top_speed_kmh == pytest.approx(18, abs=0.5)
    assert s.observed_s == pytest.approx(10, abs=0.05)


def test_jitter_does_not_add_distance() -> None:
    """A player standing still with 0.3 m box jitter: raw frame-to-frame distance would be ~250 m in 20 s."""
    s = track_stats(1, *_path([(20, 0.0)], noise_m=0.3), fps=FPS)
    assert s.distance_m < 15 and s.sprints == 0 and s.zones_m["walk"] == pytest.approx(s.distance_m, rel=0.05)


def test_sprint_is_counted_with_its_top_speed_and_time() -> None:
    f, ts, x, y = _path([(3, 2.0), (2, 8.5), (3, 2.0)], noise_m=0.05)  # 30.6 km/h for 2 s at 3–5 s
    s = track_stats(7, f, ts, x, y, fps=FPS)
    assert s.sprints == 1
    assert s.top_speed_kmh == pytest.approx(30.6, abs=1.5)
    assert 3000 <= (s.top_speed_ms or 0) <= 5000
    assert 12 < s.zones_m["sprint"] < 18  # 17 m at 8.5 m/s; the 0.3 s smoothing blurs the ramps


def test_short_burst_is_not_a_sprint() -> None:
    s = track_stats(7, *_path([(3, 2.0), (0.6, 8.5), (3, 2.0)]), fps=FPS)
    assert s.sprints == 0


def test_a_jump_of_h_is_discarded_and_gaps_split_runs() -> None:
    f, ts, x, y = _path([(10, 3.0)])
    x = x.copy()
    x[125:] += 15.0  # the homography jumps 15 m mid-run
    s = track_stats(1, f, ts, x, y, fps=FPS)
    assert s.distance_m == pytest.approx(30, rel=0.1)  # not 45
    # a 0.6 s stretch alone (between two gaps) is too short to count
    keep = np.r_[np.arange(0, 50), np.arange(60, 75), np.arange(85, 135)]
    s2 = track_stats(1, f[keep], ts[keep], x[keep] - 0, y[keep], fps=FPS)
    assert s2.observed_s == pytest.approx(4.0, abs=0.05)


def test_average_position_and_spread() -> None:
    rng = np.random.default_rng(1)
    n = 250
    f = np.arange(n)
    x, y = 40 + rng.normal(0, 3, n), 20 + rng.normal(0, 1, n)
    s = track_stats(1, f, f * 40, x, y, fps=FPS)
    assert s.avg_x == pytest.approx(40, abs=0.5) and s.avg_y == pytest.approx(20, abs=0.3)
    assert s.cov is not None and s.cov[0] == pytest.approx(9, rel=0.25) and s.cov[1] == pytest.approx(1, rel=0.25)


def test_run_stats_filters_ball_untracked_and_weak_h(tmp_path: Path) -> None:
    f, ts, x, y = _path([(4, 4.0)])
    n = len(f)
    rows = []
    for tid, cls, q in ((1, CLS_PLAYER, 0.9), (2, CLS_PLAYER, 0.2), (-1, CLS_BALL, 0.9)):
        rows.append({"frame_idx": f, "ts_ms": ts, "track_id": np.full(n, tid), "cls": np.full(n, cls),
                     "team": np.zeros(n), "x1": x * 0, "y1": x * 0, "x2": x * 0, "y2": x * 0, "conf": np.ones(n),
                     "px": x, "py": y, "h_quality": np.full(n, q), "interpolated": np.zeros(n, bool)})  # fmt: skip
    cols = {k: np.concatenate([r[k] for r in rows]) for k in rows[0]}
    cols["track_id"] = cols["track_id"].astype(np.int32)
    path = write_tracks(tmp_path / "tracks.parquet", cols, {"stage": "S5"})
    out = run_stats(path, FPS)
    assert [s.track_id for s in out] == [1]
    assert out[0].distance_m == pytest.approx(16, rel=0.03)
    assert out[0].metrics()["version"] == "pv-stats/v1"
