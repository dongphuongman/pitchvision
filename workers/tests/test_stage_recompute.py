"""M1-BE-20 manual calibration recompute: synthetic tracks.parquet + a known homography → px/py re-projected."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from cv_core.io.parquet import read_tracks, write_tracks
from cv_core.pitch import FIFA_105x68, project
from pitchvision_schemas import PipelineConfig, Shot, ShotTimeline, ShotType
from pitchvision_worker.stages.recompute import ManualCalibrationInvalid, manual_homography, run_recompute

H_P2I = np.array([[10.0, 2.0, 100.0], [0.0, 8.0, 50.0], [0.0, 0.002, 1.0]])  # pitch metres → image px (perspective)
TL = ShotTimeline(shots=[Shot(t0_ms=0, t1_ms=4000, type=ShotType.main_wide, conf=0.9),
                         Shot(t0_ms=4000, t1_ms=6000, type=ShotType.close_up, conf=0.9),
                         Shot(t0_ms=6000, t1_ms=10000, type=ShotType.main_wide, conf=0.9)])
PLAYER = (30.0, 20.0)  # where the tracked player stands (metres)
LANDMARKS = (0, 24, 29, 5, 13)


def _points(frame_ms: int = 1000, idx: tuple[int, ...] = LANDMARKS) -> list[dict[str, float]]:
    img = project(H_P2I, FIFA_105x68.keypoints[list(idx)])
    return [{"template_index": i, "x": float(x), "y": float(y), "frame_ms": frame_ms} for i, (x, y) in zip(idx, img,
                                                                                                         strict=True)]


def _tracks(path: Path) -> Path:
    """One player in both wide shots (frames 0–99 and 150–249); in the 2nd one, frames < 200 have a 'good automatic'
    H (h_quality 0.9, px/py = sentinel 1.0), the rest none (NaN)."""
    frames = np.r_[np.arange(0, 100), np.arange(150, 250)]
    fx, fy = project(H_P2I, np.array([PLAYER]))[0]
    n = len(frames)
    good = (frames >= 150) & (frames < 200)
    cols = {
        "frame_idx": frames.astype(np.int32), "ts_ms": (frames * 40).astype(np.int32),
        "track_id": np.ones(n, np.int32), "cls": np.zeros(n, np.int8), "team": np.zeros(n, np.int8),
        "x1": np.full(n, round(fx) - 10), "y1": np.full(n, round(fy) - 40), "x2": np.full(n, round(fx) + 10),
        "y2": np.full(n, round(fy)), "conf": np.full(n, 0.9, np.float16),
        "px": np.where(good, 1.0, np.nan).astype(np.float32), "py": np.where(good, 1.0, np.nan).astype(np.float32),
        "h_quality": np.where(good, 0.9, np.nan).astype(np.float16), "interpolated": np.zeros(n, bool),
    }
    return write_tracks(path, cols, {"stage": "S5"})


def _read(path: Path) -> dict[str, np.ndarray]:
    t = read_tracks(path)
    return {c: t.column(c).to_numpy(zero_copy_only=False) for c in t.column_names}


def test_manual_homography_recovers_known_h() -> None:
    H, rmse = manual_homography(_points())
    H_true = np.linalg.inv(H_P2I)
    assert rmse < 1e-6
    np.testing.assert_allclose(H, H_true / H_true[2, 2], rtol=1e-6, atol=1e-9)


def test_scope_shot_reprojects_only_the_calibrated_shot(tmp_path: Path) -> None:
    out = run_recompute(_tracks(tmp_path / "in.parquet"), TL, tmp_path / "out", PipelineConfig(), points=_points(),
                        frame_ms=1000, scope="shot")
    c = _read(out.paths["tracks"])
    first = c["frame_idx"] < 100
    # box bottom-centre is integer-rounded: ≤ ~0.1 m error
    assert np.allclose(c["px"][first], PLAYER[0], atol=0.15) and np.allclose(c["py"][first], PLAYER[1], atol=0.15)
    assert np.all(c["h_quality"][first] == 1.0)
    # the other shot is untouched (good automatic frames and frames without H alike)
    rest = ~first
    assert np.all(c["px"][rest & (c["frame_idx"] < 200)] == 1.0) and np.all(np.isnan(c["px"][c["frame_idx"] >= 200]))
    assert out.rows == 100 and out.shot == (0, 4000) and out.frames == 100
    assert out.pitch["status"] == "manual"
    # main_wide frames: 0–99 manual (100) + 150–199 good (50) of 200
    assert out.pitch["homography_ok_pct"] == 75.0
    # everything else is the same tracks table
    for k in ("frame_idx", "track_id", "team", "x1", "y2"):
        np.testing.assert_array_equal(c[k], _read(tmp_path / "in.parquet")[k])


def test_scope_video_fills_weak_frames_but_keeps_good_ones(tmp_path: Path) -> None:
    out = run_recompute(_tracks(tmp_path / "in.parquet"), TL, tmp_path / "out", points=_points(), frame_ms=1000,
                        scope="video")
    c = _read(out.paths["tracks"])
    weak = c["frame_idx"] >= 200
    assert np.allclose(c["px"][weak], PLAYER[0], atol=0.15)
    assert np.all(c["px"][(c["frame_idx"] >= 150) & (c["frame_idx"] < 200)] == 1.0)
    assert out.rows == 150 and out.pitch["homography_ok_pct"] == 100.0


def test_invalid_calibrations_are_rejected(tmp_path: Path) -> None:
    tracks = _tracks(tmp_path / "in.parquet")
    with pytest.raises(ManualCalibrationInvalid):  # frame inside the close-up
        run_recompute(tracks, TL, tmp_path / "o1", points=_points(5000), frame_ms=5000)
    collinear = [{"template_index": i, "x": 100.0 + 10 * k, "y": 50.0, "frame_ms": 0}
                 for k, i in enumerate((0, 13, 24, 1))]  # fmt: skip
    with pytest.raises(ManualCalibrationInvalid) as ei:
        run_recompute(tracks, TL, tmp_path / "o2", points=collinear, frame_ms=0)
    assert ei.value.code == "HOMOGRAPHY_UNAVAILABLE" and ei.value.retryable is False
    with pytest.raises(ManualCalibrationInvalid):
        manual_homography(_points()[:3])
