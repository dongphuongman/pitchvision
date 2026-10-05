"""M1-CV-03 — metric library on synthetic data."""
from __future__ import annotations

import math

import numpy as np
import pytest
from pveval.metrics import (
    NON_WIDE,
    WIDE,
    Box,
    average_precision,
    by_group,
    detection_ap50,
    group_ids,
    homography_report,
    iou_matrix,
    per_second_labels,
    project,
    reprojection_errors,
    shot_accuracy_per_second,
    shot_report,
    team_accuracy,
)

# --- detection ---------------------------------------------------------------------------------


def test_iou_matrix() -> None:
    a = np.array([[0, 0, 10, 10]])
    b = np.array([[0, 0, 10, 10], [5, 0, 15, 10], [20, 20, 30, 30], [0, 0, 0, 0]])
    np.testing.assert_allclose(iou_matrix(a, b)[0], [1.0, 50 / 150, 0.0, 0.0])


def test_average_precision_known_values() -> None:
    assert average_precision([True, True], 2) == pytest.approx(1.0)
    assert average_precision([False, True], 1) == pytest.approx(0.5)      # FP ranked above the TP
    assert average_precision([True, False], 1) == pytest.approx(1.0)      # FP after full recall is free
    assert average_precision([True], 2) == pytest.approx(51 / 101)        # recall 0.5 → 51 of 101 thresholds
    assert average_precision([], 3) == 0.0
    assert math.isnan(average_precision([True], 0))


def gt(img: str, cls: int, x: float) -> Box:
    return Box(img, cls, x, 0, x + 10, 20)


def test_detection_perfect_and_per_class() -> None:
    gts = [gt("a", 0, 0), gt("a", 0, 50), gt("b", 3, 100)]
    preds = [Box(g.image_id, g.cls, g.x1 + 1, g.y1, g.x2 + 1, g.y2, 0.9) for g in gts]
    r = detection_ap50(preds, gts)
    assert r.ap50 == {0: pytest.approx(1.0), 3: pytest.approx(1.0)} and r.map50 == pytest.approx(1.0)


def test_detection_duplicates_wrong_image_and_missing_class() -> None:
    gts = [gt("a", 0, 0), gt("a", 2, 50)]
    preds = [
        Box("a", 0, 0, 0, 10, 20, 0.9),
        Box("a", 0, 0, 0, 10, 20, 0.8),     # duplicate → FP (after full recall, AP unaffected)
        Box("b", 0, 0, 0, 10, 20, 0.95),    # image without GT of this class → FP ranked first
        Box("a", 1, 0, 0, 10, 20, 0.99),    # class 1 has no GT → ignored
    ]
    r = detection_ap50(preds, gts)
    assert r.ap50[0] == pytest.approx(0.5)
    assert r.ap50[2] == 0.0                   # GT never detected
    assert 1 not in r.ap50
    assert r.map50 == pytest.approx(0.25)
    assert r.n_gt == {0: 1, 2: 1} and r.n_pred[0] == 3


def test_detection_iou_threshold() -> None:
    g = [gt("a", 0, 0)]
    p = [Box("a", 0, 6, 0, 16, 20, 0.9)]     # IoU 4/16 = 0.25
    assert detection_ap50(p, g).ap50[0] == 0.0
    assert detection_ap50(p, g, iou_thr=0.2).ap50[0] == pytest.approx(1.0)


# --- teams -------------------------------------------------------------------------------------


def test_team_accuracy_permutation_invariant() -> None:
    truth = [0, 0, 1, 1, -1, 1]
    assert team_accuracy([0, 0, 1, 1, 0, 1], truth).accuracy == 1.0
    swapped = team_accuracy([1, 1, 0, 0, 1, 0], truth)
    assert swapped.accuracy == 1.0 and swapped.swapped and swapped.n == 5
    r = team_accuracy([1, 1, 0, 1, 0, -1], truth)    # swapped mapping, one wrong, one missing
    assert (r.correct, r.n, r.swapped) == (3, 5, True)
    with pytest.raises(ValueError):
        team_accuracy([0], [0, 1])


# --- shots -------------------------------------------------------------------------------------


def test_shot_report_binary_collapse() -> None:
    pred = [WIDE, "close_up", "replay", WIDE, None, WIDE]
    truth = [WIDE, NON_WIDE, NON_WIDE, NON_WIDE, WIDE, None]
    r = shot_report(pred, truth)
    assert (r.n, r.correct, r.tp, r.fp, r.fn) == (5, 3, 1, 1, 1)
    assert r.accuracy == pytest.approx(0.6) and r.wide_precision == 0.5 and r.wide_recall == 0.5


def test_shot_report_multiclass() -> None:
    r = shot_report(["close_up", "replay", WIDE], ["close_up", "close_up", WIDE])
    assert r.correct == 2 and r.wide_precision == 1.0


def test_shot_accuracy_per_second() -> None:
    truth = [(0, 3000, WIDE), (3000, 5000, "close_up"), (5000, 10000, WIDE)]
    pred = [(0, 3600, WIDE), (3600, 5600, "close_up"), (5600, 10000, WIDE)]   # boundaries late
    assert per_second_labels(truth, 4) == [WIDE, WIDE, WIDE, "close_up"]
    r = shot_accuracy_per_second(pred, truth, 10)
    # seconds 3 (3.5 s) and 5 (5.5 s) disagree
    assert (r.n, r.correct) == (10, 8)


# --- homography --------------------------------------------------------------------------------


def test_reprojection_errors() -> None:
    H = np.array([[2.0, 0, 1], [0, 2, -1], [0, 0, 1]])
    src = np.array([[0, 0], [1, 1], [10, 5]])
    np.testing.assert_allclose(project(H, src), [[1, -1], [3, 1], [21, 9]])
    dst = project(H, src) + np.array([[0, 0], [3, 4], [0, 0]])
    np.testing.assert_allclose(reprojection_errors(H, src, dst), [0, 5, 0])
    Hp = np.array([[1.0, 0, 0], [0, 1, 0], [0.001, 0, 1]])   # projective: divides by w
    np.testing.assert_allclose(project(Hp, [[100, 50]]), [[100 / 1.1, 50 / 1.1]])


def test_homography_report() -> None:
    r = homography_report([0.2, 0.8, 1.5, None], threshold=1.0)
    assert (r.n_frames, r.n_failed) == (4, 1)
    assert r.pct_within == pytest.approx(0.5)              # failed frame counts in the denominator
    assert r.mean_error == pytest.approx(2.5 / 3) and r.median_error == pytest.approx(0.8)
    assert math.isnan(homography_report([None]).mean_error)


# --- strata ------------------------------------------------------------------------------------


def test_group_pooling() -> None:
    strata = {"m1": {"cat": "men"}, "m2": {"cat": "men"}, "w1": {"cat": "women"}, "x": {"cat": None}}
    assert group_ids(strata, "cat") == {"men": ["m1", "m2"], "unknown": ["x"], "women": ["w1"]}
    correct, n = {"m1": 9, "m2": 1, "w1": 5, "x": 0}, {"m1": 10, "m2": 10, "w1": 5, "x": 1}
    [t] = by_group(strata, ["cat"], lambda ids: {"acc": sum(correct[i] for i in ids) / sum(n[i] for i in ids)})
    assert t.key == "cat" and t.rows["men"]["acc"] == pytest.approx(0.5)   # pooled, not mean of 0.9 & 0.1
    assert t.rows["women"]["acc"] == 1.0
