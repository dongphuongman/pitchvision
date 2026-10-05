"""Homography eval: label / prediction formats, field error, quality report, GT homography."""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest
from pveval.formats import (
    HomographyPred,
    PitchLabel,
    read_homographies,
    read_pitch_labels,
    write_homographies,
    write_pitch_labels,
)
from pveval.metrics import field_errors, quality_report
from run_homography import FrameResult, _row, _scale_H, gt_homography

SIZE = (1280, 720)


def _camera() -> np.ndarray:
    """pitch → image of a main camera (same construction as cv-core's pitch tests)."""
    w, h = SIZE
    C = np.array([52.5, 108.0, -22.0])
    T = np.array([52.5, 34.0, 0.0])
    f = (T - C) / np.linalg.norm(T - C)
    r = np.cross([0.0, 0.0, 1.0], f)
    r /= np.linalg.norm(r)
    R = np.stack([r, np.cross(f, r), f])
    K = np.array([[1100.0, 0, w / 2], [0, 1100.0, h / 2], [0, 0, 1]])
    H = (K @ np.c_[R, -R @ C])[:, [0, 1, 3]]
    return H / H[2, 2]


def _label(H_p2i: np.ndarray, scale: float = 1.0) -> PitchLabel:
    from cv_core.pitch import FIFA_105x68, project

    xy = project(H_p2i, FIFA_105x68.keypoints)
    pts = {i: (float(x) * scale, float(y) * scale) for i, (x, y) in enumerate(xy)
           if 0 <= x < SIZE[0] and 0 <= y < SIZE[1]}
    return PitchLabel("vid", 5000, int(SIZE[0] * scale), int(SIZE[1] * scale), pts)


def test_label_and_prediction_roundtrip(tmp_path: Path):
    lb = PitchLabel("vid", 5000, 1280, 720, {0: (1.0, 2.0), 13: (640.5, 100.25)}, pitch=(100.0, 64.0))
    write_pitch_labels(tmp_path / "l.json", [lb])
    doc = json.loads((tmp_path / "l.json").read_text())
    assert doc["format"] == "pv-pitch-keypoints/v1" and doc["pitch_size"] == {"vid": [100.0, 64.0]}
    (back,) = read_pitch_labels(tmp_path / "l.json")
    assert back == lb
    assert back.scaled(640, 360)[13] == pytest.approx((320.25, 50.125))
    # frame_idx (25 fps) instead of t_ms
    doc["frames"][0].pop("t_ms")
    doc["frames"][0]["frame_idx"] = 250
    (tmp_path / "l2.json").write_text(json.dumps(doc))
    assert read_pitch_labels(tmp_path / "l2.json")[0].t_ms == 10000
    p = HomographyPred("vid", 5000, 1280, 720, np.eye(3).tolist(), 0.8, "ok")
    write_homographies(tmp_path / "p.json", [p, HomographyPred("vid", 6000, 1280, 720, None, None, "few")])
    preds = read_homographies(tmp_path / "p.json")
    assert preds[("vid", 5000)] == p and preds[("vid", 6000)].H is None
    with pytest.raises(ValueError):
        read_pitch_labels(tmp_path / "p.json")


def test_field_errors_and_gt_homography():
    H_p2i = _camera()
    H_gt = gt_homography(_label(H_p2i), SIZE)
    assert H_gt is not None
    true = np.linalg.inv(H_p2i)
    assert field_errors(H_gt, true, SIZE).max() < 1e-3
    # labels at half resolution: scaled into the eval frame size
    assert field_errors(gt_homography(_label(H_p2i, 0.5), SIZE), true, SIZE).max() < 0.05
    # a 2 m shift of the pitch → 2 m everywhere
    shifted = np.array([[1, 0, 2.0], [0, 1, 0], [0, 0, 1]]) @ true
    fe = field_errors(shifted, true, SIZE)
    assert fe.size > 100 and np.allclose(fe, 2.0)
    # collinear labels → no GT homography
    lb = _label(H_p2i)
    only_half = PitchLabel(lb.video, lb.t_ms, lb.width, lb.height, {i: lb.points[i] for i in (13, 14, 15, 16)})
    assert gt_homography(only_half, SIZE) is None


def test_scale_H_between_resolutions():
    H = np.linalg.inv(_camera())
    H_small = _scale_H(H.tolist(), SIZE, (640, 360))  # prediction at 1280, eval frame at 640
    p = np.array([[100.0, 200.0, 1.0]])
    a, b = (H @ (p[0] * [2, 2, 1])), H_small @ p[0]
    assert np.allclose(a[:2] / a[2], b[:2] / b[2])


def test_quality_report_and_row():
    qr = quality_report([0.95, 0.75, 0.3, None])
    assert qr.n == 4 and qr.n_with_h == 3 and qr.share_at_least[0.7] == 0.5 and qr.histogram[9] == 1
    assert qr.histogram[0] == 1  # no-H frame counts as 0
    rs = [FrameResult("v", 0, SIZE, np.eye(3), 0.9, has_gt=True, kp_err=[0.1, 0.3], field_err=0.4),
          FrameResult("v", 1, SIZE, None, None, has_gt=True, field_err=math.inf)]
    row = _row(rs)
    assert row["with_H"] == 0.5 and row["field<1m"] == 0.5
    assert row["kp_err_cm_med"] == pytest.approx(20.0) and row["field_err_cm_med"] == pytest.approx(40.0)
