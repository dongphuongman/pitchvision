"""cv_core.pitch: template, YOLO-pose decode, homography recovery, line-alignment h_quality, smoother."""
from __future__ import annotations

import numpy as np
import pytest
from cv_core.pitch import (
    FLIP_INDEX,
    KEYPOINT_KINDS,
    KEYPOINT_NAMES,
    NUM_KEYPOINTS,
    FIFA_105x68,
    HomographySmoother,
    Letterbox,
    PitchKeypointModel,
    PitchKeypoints,
    PitchTemplate,
    decode,
    estimate_homography,
    letterbox,
    line_alignment,
    line_mask,
    project,
)
from cv_core.runtime import InferenceModel, Provider, TensorInfo
from pitch_synth import camera_homography, keypoints_from_H, perturb, render_frame

SIZE = (1280, 720)
VIEWS = {
    "centre": {},
    "left_box": {"target": (20.0, 34.0), "cam_x": 40.0},
    "right_zoom": {"target": (80.0, 30.0), "cam_x": 60.0, "focal_px": 2200.0},
    "high_roll": {"target": (60.0, 40.0), "height_m": 30.0, "roll_deg": 4.0},
}


def _kp(xy: np.ndarray, conf: np.ndarray) -> PitchKeypoints:
    return PitchKeypoints(xy, conf, np.zeros(4, np.float32), 1.0, SIZE)


def _field_error_m(H_i2p: np.ndarray, H_true_p2i: np.ndarray, stat: str = "max") -> float:
    """Max (or median) pitch-metre error over a grid of on-screen pitch points."""
    gx, gy = np.meshgrid(np.linspace(0, 105, 43), np.linspace(0, 68, 29))
    pts = np.c_[gx.ravel(), gy.ravel()]
    img = project(H_true_p2i, pts)
    on = (img[:, 0] >= 0) & (img[:, 0] < SIZE[0]) & (img[:, 1] >= 0) & (img[:, 1] < SIZE[1])
    err = np.linalg.norm(project(H_i2p, img[on]) - pts[on], axis=1)
    return float(err.max() if stat == "max" else np.median(err))


# --- template ---------------------------------------------------------------------------------------


def test_template_keypoints_and_tables():
    t = FIFA_105x68
    kp = t.keypoints
    assert kp.shape == (NUM_KEYPOINTS, 2) == (len(KEYPOINT_NAMES), 2) == (len(KEYPOINT_KINDS), 2)
    assert np.allclose(t.keypoint("corner_br"), (105, 68))
    assert np.allclose(t.keypoint("l_penalty_spot"), (11, 34))
    assert np.allclose(t.keypoint("half_circle_far"), (52.5, 34 - 9.15))
    assert np.allclose(t.keypoint("l_box_front_far"), (16.5, 34 - 20.16))
    # flip table = mirror x → L - x, and is an involution
    assert sorted(FLIP_INDEX) == list(range(NUM_KEYPOINTS))
    assert all(FLIP_INDEX[FLIP_INDEX[i]] == i for i in range(NUM_KEYPOINTS))
    mirrored = np.c_[t.length - kp[:, 0], kp[:, 1]]
    assert np.allclose(mirrored, kp[list(FLIP_INDEX)])
    big = PitchTemplate(110, 75)  # markings fixed by Law 1, only the outline scales
    assert np.allclose(big.keypoint("l_box_front_far") - (0, (75 - 68) / 2), t.keypoint("l_box_front_far"))


def test_keypoints_lie_on_markings():
    """intersection / arc keypoints lie on ≥ 2 markings (a line and a circle for 14/15), spots on none,
    virtual points on exactly one (the penalty-area front line)."""
    lines = FIFA_105x68.lines(step_m=0.05)

    def n_on(p: np.ndarray) -> int:
        return sum(np.min(np.linalg.norm(ln.points - p, axis=1)) < 0.06 for ln in lines if ln.kind != "spot")

    for i, (p, kind) in enumerate(zip(FIFA_105x68.keypoints, KEYPOINT_KINDS, strict=True)):
        n = n_on(p)
        if kind == "intersection":
            assert n >= 2, KEYPOINT_NAMES[i]
        elif kind == "virtual":
            assert n == 1, KEYPOINT_NAMES[i]
        elif kind == "arc":
            assert n >= 1, KEYPOINT_NAMES[i]
        else:
            assert n == 0, KEYPOINT_NAMES[i]
    names = {ln.name for ln in lines}
    assert {"touchline_far", "halfway", "centre_circle", "penalty_arc_left", "goal_area_right_front"} <= names


# --- YOLO-pose decode -------------------------------------------------------------------------------


def _raw_output(lb: Letterbox, kp_src: np.ndarray, kconf: np.ndarray, score: float, A: int = 50) -> np.ndarray:
    """Fake ``[4+1+3K, A]`` head output with one strong anchor, a duplicate and noise anchors."""
    K = len(kp_src)
    C = 4 + 1 + 3 * K
    out = np.zeros((C, A), np.float32)
    kin = kp_src * lb.scale + [lb.pad_x, lb.pad_y]
    box = np.r_[kin.min(0), kin.max(0)]
    for a, (s, jitter) in enumerate([(score, 0.0), (score - 0.1, 3.0)]):
        out[0:2, a] = (box[:2] + box[2:]) / 2 + jitter
        out[2:4, a] = box[2:] - box[:2]
        out[4, a] = s
        k = np.c_[kin + jitter, kconf].reshape(-1)
        out[5:, a] = k
    out[4, 2:] = 0.01
    return out


def test_letterbox_and_decode_roundtrip():
    img = np.zeros((720, 1280, 3), np.uint8)
    x, lb = letterbox(img, (640, 640))
    assert x.shape == (3, 640, 640) and lb.scale == pytest.approx(0.5) and lb.pad_y == 140 and lb.pad_x == 0
    assert x[:, 0, 0] == pytest.approx(114 / 255)
    H = camera_homography()
    kp_src, kconf = keypoints_from_H(H, noise_px=0)
    res = decode(_raw_output(lb, kp_src, kconf, 0.9), lb)
    assert len(res) == 1  # duplicate suppressed by NMS, noise below conf
    assert np.allclose(res[0].xy, kp_src, atol=1e-3)
    assert np.allclose(res[0].conf, kconf) and res[0].score == pytest.approx(0.9)
    # end-to-end export layout [D, 6 + 3K]
    kin = kp_src * lb.scale + [lb.pad_x, lb.pad_y]
    e2e = np.zeros((300, 6 + 3 * NUM_KEYPOINTS), np.float32)
    e2e[0, :4] = [0, 0, 640, 640]
    e2e[0, 4] = 0.8
    e2e[0, 6:] = np.c_[kin, kconf].reshape(-1)
    r2 = decode(e2e[None], lb)
    assert len(r2) == 1 and np.allclose(r2[0].xy, kp_src, atol=1e-3)


class _FakePose(InferenceModel):
    def __init__(self, raw: np.ndarray) -> None:
        self.name, self.provider, self.raw = "fake", Provider.ort_cpu, raw
        self.inputs = (TensorInfo("images", (1, 3, 384, 640), np.dtype(np.float32)),)
        self.outputs = (TensorInfo("output0", (1, raw.shape[0], raw.shape[1]), np.dtype(np.float32)),)

    def _run(self, feeds: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
        assert feeds["images"].shape == (1, 3, 384, 640)
        return {"output0": self.raw[None]}


def test_model_wrapper_rectangular_input():
    img = np.zeros((720, 1280, 3), np.uint8)
    _, lb = letterbox(img, (384, 640))
    assert lb.pad_y == 12 and lb.scale == pytest.approx(0.5)
    kp_src, kconf = keypoints_from_H(camera_homography(), noise_px=0)
    model = PitchKeypointModel(_FakePose(_raw_output(lb, kp_src, kconf, 0.9)))
    (kp,) = model([img])
    assert np.allclose(kp.xy, kp_src, atol=1e-3) and kp.image_size == SIZE
    model_hi = PitchKeypointModel(_FakePose(_raw_output(lb, kp_src, kconf, 0.2)), conf=0.5)
    assert model_hi([img])[0].conf.max() == 0  # nothing passes → empty instance


# --- homography -------------------------------------------------------------------------------------


@pytest.mark.parametrize("view", sorted(VIEWS))
def test_recovers_homography_with_noise_and_outliers(view: str):
    H = camera_homography(**VIEWS[view])
    xy, conf = keypoints_from_H(H, noise_px=1.5, seed=1)
    vis = np.flatnonzero(conf > 0.5)
    assert len(vis) >= 6
    xy, conf = xy.copy(), conf.copy()
    # SP-03 failure mode: a couple of confident hallucinations (wrong place) + points at the border
    xy[vis[0]] += (90, -60)
    off = [i for i in range(NUM_KEYPOINTS) if conf[i] < 0.5][:2]
    xy[off[0]], conf[off[0]] = (2, 300), 0.95
    xy[off[1]], conf[off[1]] = (640, 715), 0.95
    est = estimate_homography(_kp(xy, conf))
    assert est.ok, est.reason
    assert not est.inliers[vis[0]] and not est.used[off[0]] and not est.used[off[1]]
    assert _field_error_m(est.H, H) < 1.0  # worst on-screen point (far side: 1 px ≈ 0.3 m)
    assert _field_error_m(est.H, H, "median") < 0.3
    assert est.rmse_px < 3


def test_rejects_collinear_keypoints():
    H = camera_homography()
    xy, conf = keypoints_from_H(H, noise_px=0.5)
    keep = np.zeros(NUM_KEYPOINTS, bool)
    keep[[13, 14, 15, 16]] = True  # halfway line only: SP-03 "centre view" degeneracy
    est = estimate_homography(_kp(xy, np.where(keep, conf, 0.0)))
    assert not est.ok and "collinear" in est.reason
    keep[[30, 31]] = True  # + both sides of the circle → well-posed
    assert estimate_homography(_kp(xy, np.where(keep, 0.9, 0.0))).ok


def test_rejects_mirrored_assignment():
    """Model confuses left and right halves: indices swapped by FLIP_INDEX → mirror image → reject."""
    H = camera_homography(target=(25.0, 34.0), cam_x=40.0)
    xy, conf = keypoints_from_H(H, noise_px=0.5)
    flip = list(FLIP_INDEX)
    est = estimate_homography(_kp(xy[flip], conf[flip]))
    assert not est.ok  # MAGSAC itself refuses orientation-reversing models; else the mirror check fires
    assert "mirror" in est.reason or "MAGSAC" in est.reason


def test_rejects_too_few_points():
    H = camera_homography()
    xy, conf = keypoints_from_H(H)
    conf2 = np.zeros_like(conf)
    conf2[np.flatnonzero(conf > 0.5)[:3]] = 0.9
    est = estimate_homography(_kp(xy, conf2))
    assert not est.ok and "usable" in est.reason


# --- h_quality (line alignment) ---------------------------------------------------------------------


@pytest.mark.parametrize("view", sorted(VIEWS))
def test_h_quality_high_for_true_low_for_wrong(view: str):
    H = camera_homography(**VIEWS[view])
    img = render_frame(H, seed=3, players=25, clutter=6)
    m = line_mask(img)
    good = line_alignment(m, H)
    assert good.quality > 0.9, good
    wrong = {
        "shift_x_6m": perturb(H, shift_m=(6, 0)),
        "shift_y_5m": perturb(H, shift_m=(0, 5)),
        "scale_0.85": perturb(H, scale=0.85),
        "rot_8deg": perturb(H, rot_deg=8),
        "other_half": perturb(H, shift_m=(30, 0)),
    }
    for name, Hw in wrong.items():
        q = line_alignment(m, Hw).quality
        assert q < 0.7, (name, q)
        assert q < good.quality - 0.3, (name, q)
    # graded: a small error scores between a correct and a grossly wrong calibration
    q1 = line_alignment(m, perturb(H, shift_m=(1.0, 0))).quality
    q6 = line_alignment(m, wrong["shift_x_6m"]).quality
    assert good.quality > q1 > q6


def test_h_quality_from_estimated_H_and_no_lines():
    H = camera_homography(target=(20.0, 34.0), cam_x=40.0)
    img = render_frame(H, seed=5)
    xy, conf = keypoints_from_H(H, noise_px=1.5, seed=5)
    est = estimate_homography(_kp(xy, conf))
    assert est.ok
    assert line_alignment(line_mask(img), est.H_inv).quality > 0.85
    blank = np.full_like(img, (40, 130, 45))  # grass without markings: nothing to verify against
    assert line_alignment(line_mask(blank), H).quality == 0.0
    assert line_alignment(line_mask(img), None).quality == 0.0


# --- smoother ---------------------------------------------------------------------------------------


def _H_i2p(**kw: float) -> np.ndarray:
    H = np.linalg.inv(camera_homography(**kw))  # type: ignore[arg-type]
    return H / H[2, 2]


def test_smoother_reduces_noise_and_interpolates():
    H_true = _H_i2p()
    rng = np.random.default_rng(0)
    sm = HomographySmoother(window=15)
    raw_err, sm_err = [], []
    for i in range(0, 200, 5):
        Hn = np.linalg.inv(perturb(np.linalg.inv(H_true), shift_m=tuple(rng.normal(0, 0.5, 2))))
        assert sm.update(i, Hn, 0.9)
        if i >= 50:
            raw_err.append(_field_error_m(Hn, np.linalg.inv(H_true)))
            Hs, q = sm.at(i)
            assert Hs is not None and q == pytest.approx(0.9, abs=0.05)
            sm_err.append(_field_error_m(Hs, np.linalg.inv(H_true)))
    assert np.mean(sm_err) < 0.6 * np.mean(raw_err)
    H_mid, _ = sm.at(102)  # between keyframes 100 and 105
    assert H_mid is not None and np.all(np.isfinite(H_mid))


def test_smoother_rejects_outlier_but_follows_real_move_and_resets_on_cut():
    A, B = _H_i2p(), _H_i2p(target=(30.0, 34.0), cam_x=40.0)
    sm = HomographySmoother(window=4)
    for i in (0, 5, 10):
        sm.update(i, A, 0.9)
    assert not sm.update(15, B, 0.9)  # single jump of ~20 m: outlier
    assert _field_error_m(sm.at(15)[0], np.linalg.inv(A)) < 0.05
    assert sm.update(20, B, 0.9)  # second agreeing measurement: the camera really moved
    assert _field_error_m(sm.at(20)[0], np.linalg.inv(B)) < 0.05
    assert not sm.update(25, A, 0.1)  # below min_quality
    # cut at 30: nothing before the first update of the new shot leaks over the cut
    sm.reset(30)
    assert sm.at(31) == (None, 0.0)
    sm.update(35, A, 0.8)
    Hc, q = sm.at(31)  # back-filled from the shot's first state
    assert Hc is not None and q == pytest.approx(0.8) and _field_error_m(Hc, np.linalg.inv(A)) < 0.05
    assert _field_error_m(sm.at(22)[0], np.linalg.inv(B)) < 0.05  # previous shot still queryable
    assert sm.at(35 + 51) == (None, 0.0)  # held at most max_hold frames
