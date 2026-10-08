"""M3-BE-03 data exports: tracks.parquet + frozen spec → JSON / ZIP of CSVs with shot_type + estimated flags."""
from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path

import numpy as np
from cv_core.io.parquet import CLS_BALL, CLS_PLAYER, write_tracks
from pitchvision_schemas import ExportSpec, Shot, ShotType, TrackOverride
from pitchvision_worker.data_export import TRACK_COLUMNS, build_data_export

STATS = {"teams": [{"cluster": 0, "name": "Reds", "color": "#f00", "possession_pct": 60.0, "possession_ms": 3000,
                    "passes": 2, "passes_completed": 1, "balls_lost": 1, "distance_m": 120.0,
                    "estimated_distance_m": 9000.0, "m_per_min": 130.0, "sprints": 1,
                    "top_speed": {"kmh": 31.0, "at_ms": 400, "track_id": 1}, "observed_player_s": 60.0, "tracks": 1}],
         "players": [{"track_id": 1, "cluster": 0, "role": "player", "observed_s": 60.0, "distance_m": 120.0,
                      "top_speed_kmh": 31.0, "top_speed_ms": 400, "sprints": 1, "avg_x": 30.0, "avg_y": 20.0}],
         "passes": [{"t_ms": 200, "end_ms": 600, "cluster": 0, "from_track": 1, "to_track": 2, "completed": True,
                     "x0": 10, "y0": 10, "x1": 20, "y1": 12},
                    {"t_ms": 9000, "end_ms": 9500, "cluster": 0, "from_track": 1, "to_track": 7, "completed": False,
                     "x0": 1, "y0": 1, "x1": 2, "y1": 2}],
         "possession_timeline": [{"minute": 0, "ms": {"0": 3000}}]}  # fmt: skip


def _tracks(path: Path) -> Path:
    """Frames 0–49: track 1 (player, frames 20–24 interpolated, weak H from frame 40), track 2 (overridden to
    goalkeeper of team 1), the ball."""
    f = np.arange(50)
    rows = []
    for tid, cls, team in ((1, CLS_PLAYER, 0), (2, CLS_PLAYER, 0), (-1, CLS_BALL, -1)):
        q = np.where(f >= 40, 0.2, 0.9)
        rows.append({"frame_idx": f, "ts_ms": f * 40, "track_id": np.full(50, tid), "cls": np.full(50, cls),
                     "team": np.full(50, team), "x1": f * 1.0, "y1": f * 1.0, "x2": f + 10.0, "y2": f + 30.0,
                     "conf": np.full(50, 0.8), "px": 10 + f * 0.2, "py": np.full(50, 30.0), "h_quality": q,
                     "interpolated": (f >= 20) & (f < 25) & (tid == 1)})  # fmt: skip
    cols = {k: np.concatenate([r[k] for r in rows]) for k in rows[0]}
    for k, dt in (("track_id", np.int32), ("cls", np.int8), ("team", np.int8)):
        cols[k] = cols[k].astype(dt)
    return write_tracks(path, cols, {"stage": "S5"})


def _spec(**kw: object) -> ExportSpec:
    base = {"kind": "data", "job_id": "j", "revision": 1, "from_ms": 0, "to_ms": 2000, "width": 1280, "height": 720,
            "teams": [], "video_title": "Final", "duration_ms": 2000, "stats": STATS,
            "shots": [Shot(t0_ms=0, t1_ms=1000, type=ShotType.main_wide, conf=1),
                      Shot(t0_ms=1000, t1_ms=2000, type=ShotType.close_up, conf=1)],
            "track_overrides": [TrackOverride(track_id=2, team=1, cls=1)]}  # fmt: skip
    return ExportSpec.model_validate(base | kw)


def test_json_rows_flags_and_stats(tmp_path: Path) -> None:
    out = tmp_path / "e.json"
    res = build_data_export(_spec(), _tracks(tmp_path / "t.parquet"), out)
    doc = json.loads(out.read_text())
    assert doc["schema"] == "pitchvision.data/v1" and doc["rate_hz"] == 5 and doc["video"]["title"] == "Final"
    cols = doc["tracking"]["columns"]
    assert cols == list(TRACK_COLUMNS)
    rows = [dict(zip(cols, r, strict=True)) for r in doc["tracking"]["rows"]]
    assert res.rows == len(rows) == 3 * 10  # 5 Hz = frames 0, 5, …, 45 × 3 objects
    t1 = [r for r in rows if r["track_id"] == 1]
    assert [r["estimated"] for r in t1] == [f in (20,) for f in range(0, 50, 5)]
    assert {r["shot_type"] for r in rows if r["t_ms"] < 1000} == {"main_wide"}
    assert {r["shot_type"] for r in rows if r["t_ms"] >= 1000} == {"close_up"}
    assert all(r["x_m"] is None for r in t1 if r["frame"] >= 40) and t1[0]["x_m"] == 10.0  # weak H → no position
    t2 = next(r for r in rows if r["track_id"] == 2)
    assert (t2["team"], t2["role"]) == (1, "goalkeeper")  # the SCR-14 fix wins
    ball = next(r for r in rows if r["role"] == "ball")
    assert ball["track_id"] is None and ball["team"] is None
    assert doc["teams"][0]["distance_match_estimated_m"] == 9000.0 and doc["teams"][0]["possession_pct"] == 60.0
    assert [p["t_ms"] for p in doc["passes"]] == [200]  # the 9 s pass is outside the range


def test_csv_zip_25hz_range(tmp_path: Path) -> None:
    out = tmp_path / "e.zip"
    build_data_export(_spec(data_format="csv", rate_hz=25, from_ms=400, to_ms=800), _tracks(tmp_path / "t.parquet"),
                      out)  # fmt: skip
    with zipfile.ZipFile(out) as z:
        assert set(z.namelist()) == {"README.txt", "tracking.csv", "teams.csv", "players.csv", "passes.csv",
                                     "shots.csv", "meta.json"}  # fmt: skip
        tr = list(csv.DictReader(io.StringIO(z.read("tracking.csv").decode())))
        assert len(tr) == 10 * 3 and {r["t_ms"] for r in tr} == {str(40 * f) for f in range(10, 20)}
        assert {r["estimated"] for r in tr if r["track_id"] == "1"} == {"0"}  # frames 10–19: detected
        teams = list(csv.DictReader(io.StringIO(z.read("teams.csv").decode())))
        assert teams[0]["distance_match_estimated_m"] == "9000.0" and teams[0]["top_speed_track_id"] == "1"
        assert "ESTIMATE" in z.read("README.txt").decode()
        assert list(csv.DictReader(io.StringIO(z.read("passes.csv").decode()))) == []  # pass at 200 ms is outside
