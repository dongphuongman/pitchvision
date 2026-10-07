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


# ---------------------------------------------------------------- camera tracking over the calibrated shot
PAN_PX = 4  # the camera pans right by 4 px a frame: frame k shows scene columns [4k, 4k + W)
W, H_IMG = 640, 360


def _panning_video(path: Path, n: int = 100) -> Path:
    import av
    import cv2

    rng = np.random.default_rng(3)
    noise = rng.integers(0, 255, (H_IMG // 8, (W + PAN_PX * n) // 8), np.uint8)
    scene = cv2.GaussianBlur(cv2.resize(noise, (W + PAN_PX * n, H_IMG), interpolation=cv2.INTER_NEAREST), (5, 5), 0)
    scene = cv2.cvtColor(scene, cv2.COLOR_GRAY2BGR)
    with av.open(str(path), "w") as out:
        st = out.add_stream("libx264", rate=25)
        st.width, st.height, st.pix_fmt = W, H_IMG, "yuv420p"
        st.options = {"crf": "12", "preset": "ultrafast"}
        for k in range(n):
            frame = av.VideoFrame.from_ndarray(np.ascontiguousarray(scene[:, PAN_PX * k: PAN_PX * k + W]), "bgr24")
            out.mux(st.encode(frame))
        out.mux(st.encode(None))
    return path


def _panning_tracks(path: Path) -> Path:
    """The player stands still on the pitch for the whole first shot (frames 0–99); the camera pans, so the box
    moves left in the image by PAN_PX a frame (scene coordinates = frame-0 image coordinates)."""
    frames = np.arange(0, 100)
    sx, sy = project(H_P2I, np.array([PLAYER]))[0]
    x = sx - PAN_PX * frames
    n = len(frames)
    cols = {
        "frame_idx": frames.astype(np.int32), "ts_ms": (frames * 40).astype(np.int32),
        "track_id": np.ones(n, np.int32), "cls": np.zeros(n, np.int8), "team": np.zeros(n, np.int8),
        "x1": x - 10, "y1": np.full(n, sy - 40), "x2": x + 10, "y2": np.full(n, sy),
        "conf": np.full(n, 0.9, np.float16),
        "px": np.full(n, np.nan, np.float32), "py": np.full(n, np.nan, np.float32),
        "h_quality": np.full(n, np.nan, np.float16), "interpolated": np.zeros(n, bool),
    }  # fmt: skip
    return write_tracks(path, cols, {"stage": "S5"})


def _clicks_on_frame(k: int) -> list[dict[str, float]]:
    pts = _points(frame_ms=k * 40)
    return [{**p, "x": p["x"] - PAN_PX * k} for p in pts]  # the same landmarks, seen in frame k


def test_manual_h_follows_the_camera_through_the_shot(tmp_path: Path) -> None:
    video, tracks = _panning_video(tmp_path / "normalized.mp4"), _panning_tracks(tmp_path / "in.parquet")
    out = run_recompute(tracks, TL, tmp_path / "out", PipelineConfig(), points=_clicks_on_frame(50), frame_ms=2000,
                        scope="shot", normalized=video)
    c = _read(out.paths["tracks"])
    err = np.hypot(c["px"] - PLAYER[0], c["py"] - PLAYER[1])
    assert np.all(np.isfinite(err)) and float(err.max()) < 0.5, float(np.nanmax(err))
    assert out.stats["camera_tracked_frames"] >= 98  # frame 0 has no motion estimate before it; it's reached backwards
    q = c["h_quality"].astype(np.float32)
    assert q[50] == pytest.approx(1.0, abs=1e-3) and q[0] < q[25] < q[50] > q[75] > q[99]  # decays with distance
    assert float(q.min()) >= 0.5 - 1e-3  # ≤ 2 s from the click at 0.002 / frame


def test_without_the_video_the_fixed_h_drifts_with_the_camera(tmp_path: Path) -> None:
    tracks = _panning_tracks(tmp_path / "in.parquet")
    out = run_recompute(tracks, TL, tmp_path / "out", PipelineConfig(), points=_clicks_on_frame(50), frame_ms=2000,
                        scope="shot")
    c = _read(out.paths["tracks"])
    err = np.hypot(c["px"] - PLAYER[0], c["py"] - PLAYER[1])
    assert float(err[50]) < 0.15 and float(np.nanmax(err)) > 5.0  # right on the clicked frame only
    assert out.stats["camera_tracked_frames"] == 0


def test_an_unreadable_video_falls_back_to_the_fixed_h(tmp_path: Path) -> None:
    bad = tmp_path / "normalized.mp4"
    bad.write_bytes(b"not a video")
    out = run_recompute(_panning_tracks(tmp_path / "in.parquet"), TL, tmp_path / "out", PipelineConfig(),
                        points=_clicks_on_frame(50), frame_ms=2000, scope="shot", normalized=bad)
    assert out.stats["camera_tracked_frames"] == 0 and out.rows == 100
