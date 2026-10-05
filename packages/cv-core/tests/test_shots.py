"""S2b shot classifier pieces (M1-CV-06): numpy GBM, features, cuts, timeline, D-FINE post-process."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import numpy as np
import pytest
from cv_core.detect import Detections, postprocess
from cv_core.shots import (
    COLOUR_FEATURES,
    PERSON_FEATURES,
    CutDetector,
    GbmModel,
    ShotClassifier,
    ShotProbs,
    Tree,
    build_timeline,
    colour_features,
    person_features,
    segment_video,
)
from pitchvision_schemas import ShotType

# --- numpy GBM ------------------------------------------------------------------------------------------------------


def stump(feature: int, thr: float, lo: float, hi: float) -> Tree:
    return Tree(feature=np.array([feature, 0, 0], np.int32), threshold=np.array([thr, 0, 0.0]),
                left=np.array([1, -1, -1], np.int32), right=np.array([2, -1, -1], np.int32),
                value=np.array([0.0, lo, hi]), nan_left=np.array([False, False, False]))


def binary_model(features: tuple[str, ...] = COLOUR_FEATURES) -> GbmModel:
    # logit(non_wide) = 0 + (green <= 0.5 ? +3 : -3)  →  lots of grass → main_wide
    return GbmModel(name="t", version="1", classes=("main_wide", "non_wide"), features=features,
                    baseline=np.zeros(1), trees=((stump(0, 0.5, 3.0, -3.0),),), class_map={"non_wide": "other"})


def test_gbm_binary_and_nan_routing() -> None:
    m = binary_model()
    X = np.zeros((3, len(COLOUR_FEATURES)))
    X[0, 0], X[1, 0], X[2, 0] = 0.9, 0.1, np.nan
    p = m.predict_proba(X)
    s = 1 / (1 + np.exp(-3.0))
    assert np.allclose(p[0], [s, 1 - s]) and np.allclose(p[1], [1 - s, s])
    assert np.allclose(p[2], p[0])  # NaN goes right (nan_left False)


def test_gbm_multiclass_softmax_and_roundtrip(tmp_path: Path) -> None:
    classes = ("main_wide", "close_up", "replay", "other")
    trees = ((stump(0, 0.5, -1, 2), stump(1, 0.5, 1, 0), stump(1, 0.5, 0, 0), stump(0, 0.5, 0.5, 0)),)
    m = GbmModel(name="m4", version="2", classes=classes, features=("a", "b"), baseline=np.array([0.1, 0, 0, 0]),
                 trees=trees)
    X = np.array([[0.9, 0.1], [0.1, 0.9]])
    raw = np.array([[0.1 + 2, 1, 0, 0], [0.1 - 1, 0, 0, 0.5]])
    want = np.exp(raw) / np.exp(raw).sum(1, keepdims=True)
    assert np.allclose(m.predict_proba(X), want)
    m.save(tmp_path / "m.json")
    m2 = GbmModel.load(tmp_path / "m.json")
    assert m2.classes == classes and np.allclose(m2.predict_proba(X), want)
    bad = json.loads((tmp_path / "m.json").read_text()) | {"schema_version": 99}
    with pytest.raises(ValueError):
        GbmModel.from_json(bad)


def test_classifier_maps_to_shot_types() -> None:
    clf = ShotClassifier(binary_model())
    img_grass = np.zeros((360, 640, 3), np.uint8)
    img_grass[:] = (40, 160, 40)  # BGR green
    img_crowd = np.full((360, 640, 3), 90, np.uint8)
    probs = clf.classify([img_grass, img_crowd])
    assert probs[0].type is ShotType.main_wide and probs[1].type is ShotType.other
    assert probs[0].probs.shape == (4,) and probs[0].probs[1] == 0 and probs[0].probs[2] == 0
    with pytest.raises(ValueError, match="needs person features"):
        ShotClassifier(binary_model(COLOUR_FEATURES + PERSON_FEATURES))
    with pytest.raises(ValueError, match="do not match"):
        ShotClassifier(binary_model(("x",)))


def test_colour_features_resolution_invariant() -> None:
    rng = np.random.default_rng(0)
    small = rng.integers(0, 255, (90, 160, 3), dtype=np.uint8)
    import cv2

    big = cv2.resize(small, (1280, 720), interpolation=cv2.INTER_NEAREST)
    mid = cv2.resize(small, (640, 360), interpolation=cv2.INTER_NEAREST)
    a, b = colour_features(big), colour_features(mid)
    assert a.shape == (len(COLOUR_FEATURES),)
    assert np.allclose(a[:3], b[:3], atol=0.02)


def test_person_features() -> None:
    d = Detections(np.array([[0, 0, 10, 72], [0, 0, 10, 36], [0, 0, 5, 5]], np.float32),
                   np.array([0.9, 0.8, 0.7], np.float32), np.array([0, 0, 32]))
    f = person_features(d, 720)
    assert f.tolist() == pytest.approx([2, 0.1, 0.075, 0.075, 1])
    assert person_features(Detections(np.zeros((0, 4)), np.zeros(0), np.zeros(0, int)), 720).tolist() == [0] * 5


# --- D-FINE post-process ---------------------------------------------------------------------------------------------


def test_dfine_postprocess_topk_threshold_and_boxes() -> None:
    logits = np.full((1, 3, 4), -10.0, np.float32)
    logits[0, 0, 0] = 2.0   # person 0.88
    logits[0, 1, 3] = 0.0   # class 3, 0.5
    logits[0, 2, 1] = -2.0  # 0.12 → below threshold
    boxes = np.array([[[0.5, 0.5, 0.2, 0.4], [0.25, 0.25, 0.1, 0.1], [0.5, 0.5, 1, 1]]], np.float32)
    d = postprocess(logits, boxes, [(100, 200)], conf=0.35)[0]
    assert d.labels.tolist() == [0, 3]
    assert np.allclose(d.scores, [1 / (1 + np.exp(-2)), 0.5])
    assert np.allclose(d.boxes[0], [80, 30, 120, 70]) and np.allclose(d.boxes[1], [40, 20, 60, 30])


# --- cuts ------------------------------------------------------------------------------------------------------------


def _scene(colour: tuple[int, int, int], t: int, rng: np.random.Generator) -> np.ndarray:
    img = np.zeros((180, 320, 3), np.uint8)
    img[:] = colour
    x = (t * 7) % 280  # a moving bright block: motion inside a shot
    img[60:120, x:x + 40] = 230
    return np.clip(img.astype(int) + rng.integers(-6, 7, img.shape), 0, 255).astype(np.uint8)


def test_cut_detector_fires_on_cuts_only() -> None:
    rng = np.random.default_rng(1)
    det = CutDetector()
    colours = [(40, 160, 40), (60, 60, 180), (40, 150, 50), (200, 200, 200)]
    cuts = [i for i in range(200) if det.update(i, _scene(colours[i // 50], i, rng))]
    assert cuts == [50, 100, 150]


# --- timeline --------------------------------------------------------------------------------------------------------

WIDE = ShotProbs(np.array([0.9, 0, 0, 0.1]))
OTHER = ShotProbs(np.array([0.2, 0, 0, 0.8]))


def test_timeline_cuts_smoothing_and_merge() -> None:
    # 0-10 s wide (one noisy sample at 4 s), cut at 10.0 s, 10-14 s other, 0.4 s blip at 14.0-14.4 s, wide to 20 s
    samples = [(t * 1000, OTHER if t in (4, 10, 11, 12, 13) else WIDE) for t in range(20)]
    tl = build_timeline(samples, [10_000, 14_000, 14_400], 20_000, min_shot_ms=1000)
    got = [(s.t0_ms, s.t1_ms, s.type) for s in tl.shots]
    assert got == [(0, 10_000, ShotType.main_wide), (10_000, 14_000, ShotType.other),
                   (14_000, 20_000, ShotType.main_wide)]
    assert all(0 <= s.conf <= 1 for s in tl.shots)
    assert tl.coverage() == pytest.approx(0.8)


def test_timeline_splits_on_persistent_change_without_cut() -> None:
    samples = [(t * 1000, WIDE if t < 6 else OTHER) for t in range(12)]
    tl = build_timeline(samples, [], 12_000)
    assert [(s.t0_ms, s.t1_ms, s.type) for s in tl.shots] == [(0, 5500, ShotType.main_wide),
                                                            (5500, 12_000, ShotType.other)]


def test_timeline_edge_cases() -> None:
    assert build_timeline([], [], 0).shots == []
    tl = build_timeline([], [500], 3000)
    assert len(tl.shots) == 1 and tl.shots[0].type is ShotType.other
    tl = build_timeline([(0, WIDE)], [], 700)  # shorter than min_shot_ms: still one shot
    assert [(s.t0_ms, s.t1_ms, s.type) for s in tl.shots] == [(0, 700, ShotType.main_wide)]


# --- whole video -----------------------------------------------------------------------------------------------------


def test_segment_video_synthetic(tmp_path: Path) -> None:
    """3 s grass, 2 s grey, 3 s grass at 25 fps → wide / other / wide with cuts at 3 s and 5 s."""
    import shutil

    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    path = tmp_path / "v.mp4"
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", "320x180",
                             "-r", "25", "-i", "-", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
                             "-pix_fmt", "yuv420p", str(path)], stdin=subprocess.PIPE)
    assert proc.stdin is not None
    rng = np.random.default_rng(2)
    for i in range(200):
        colour = (90, 90, 90) if 75 <= i < 125 else (40, 160, 40)
        proc.stdin.write(_scene(colour, i, rng).tobytes())
    proc.stdin.close()
    assert proc.wait(60) == 0
    res = segment_video(path, ShotClassifier(binary_model()))
    assert res.cuts_ms == [3000, 5000]
    assert [(s.t0_ms, s.t1_ms, s.type) for s in res.timeline.shots] == [
        (0, 3000, ShotType.main_wide), (3000, 5000, ShotType.other), (5000, 8000, ShotType.main_wide)]
    assert len(res.samples) == 8


MODEL = os.environ.get("PITCHVISION_SHOTS_MODEL")


@pytest.mark.skipif(not MODEL or not Path(MODEL).is_file(), reason="PITCHVISION_SHOTS_MODEL not set")
def test_real_model_loads() -> None:
    m = GbmModel.load(MODEL)  # type: ignore[arg-type]
    assert m.classes[0] == "main_wide" and len(m.trees) > 0
