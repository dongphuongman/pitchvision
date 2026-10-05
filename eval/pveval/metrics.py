"""Golden-eval metrics (AD §18): pure functions on plain data, no I/O.

- detection AP@0.5 per class + mAP (COCO-style 101-point interpolation)
- team-assignment accuracy, invariant to the 0/1 cluster permutation
- shot classification accuracy (per sample / per second) + main_wide precision/recall
- homography reprojection error and share of frames under a threshold
- ``group_ids`` to report every metric per stratum ("báo cáo theo từng nhóm")
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

# --- detection ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Box:
    image_id: str
    cls: int
    x1: float
    y1: float
    x2: float
    y2: float
    score: float = 1.0

    @property
    def xyxy(self) -> tuple[float, float, float, float]:
        return self.x1, self.y1, self.x2, self.y2


def iou_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """IoU between (N,4) and (M,4) xyxy boxes → (N,M)."""
    a, b = np.asarray(a, float).reshape(-1, 4), np.asarray(b, float).reshape(-1, 4)
    ix1 = np.maximum(a[:, None, 0], b[None, :, 0])
    iy1 = np.maximum(a[:, None, 1], b[None, :, 1])
    ix2 = np.minimum(a[:, None, 2], b[None, :, 2])
    iy2 = np.minimum(a[:, None, 3], b[None, :, 3])
    inter = np.clip(ix2 - ix1, 0, None) * np.clip(iy2 - iy1, 0, None)
    area_a = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    area_b = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    union = area_a[:, None] + area_b[None, :] - inter
    return np.where(union > 0, inter / np.where(union > 0, union, 1), 0.0)


def average_precision(tp: Sequence[bool], n_gt: int) -> float:
    """COCO 101-point interpolated AP from TP flags of score-sorted detections."""
    if n_gt == 0:
        return float("nan")
    tp_a = np.asarray(tp, bool)
    if tp_a.size == 0:
        return 0.0
    ctp, cfp = np.cumsum(tp_a), np.cumsum(~tp_a)
    recall = ctp / n_gt
    precision = ctp / (ctp + cfp)
    precision = np.maximum.accumulate(precision[::-1])[::-1]  # monotone envelope
    rs = np.linspace(0, 1, 101)
    idx = np.searchsorted(recall, rs, side="left")
    return float(np.mean([precision[i] if i < precision.size else 0.0 for i in idx]))


@dataclass(frozen=True)
class DetectionReport:
    ap50: dict[int, float]   # per class with ground truth
    n_gt: dict[int, int]
    n_pred: dict[int, int]

    @property
    def map50(self) -> float:
        vals = [v for v in self.ap50.values() if not np.isnan(v)]
        return float(np.mean(vals)) if vals else float("nan")


def detection_ap50(preds: Iterable[Box], gts: Iterable[Box], iou_thr: float = 0.5) -> DetectionReport:
    """AP@iou_thr per class. Greedy COCO matching: detections by descending score, each takes the
    best-IoU unmatched GT of its image and class. Classes without GT are ignored (COCO: -1)."""
    gt_by: dict[tuple[int, str], list[Box]] = defaultdict(list)
    for g in gts:
        gt_by[(g.cls, g.image_id)].append(g)
    pred_by: dict[int, list[Box]] = defaultdict(list)
    for p in preds:
        pred_by[p.cls].append(p)
    classes = sorted({c for c, _ in gt_by})
    ap, n_gt, n_pred = {}, {}, {}
    for c in classes:
        gt_c = {img: np.array([b.xyxy for b in bs]) for (cc, img), bs in gt_by.items() if cc == c}
        used = {img: np.zeros(len(a), bool) for img, a in gt_c.items()}
        dets = sorted(pred_by.get(c, []), key=lambda b: -b.score)
        tp = []
        for d in dets:
            boxes = gt_c.get(d.image_id)
            if boxes is None:
                tp.append(False)
                continue
            ious = iou_matrix(np.array([d.xyxy]), boxes)[0]
            ious[used[d.image_id]] = -1
            j = int(np.argmax(ious))
            if ious[j] >= iou_thr:
                used[d.image_id][j] = True
                tp.append(True)
            else:
                tp.append(False)
        n_gt[c] = sum(len(a) for a in gt_c.values())
        n_pred[c] = len(dets)
        ap[c] = average_precision(tp, n_gt[c])
    return DetectionReport(ap, n_gt, n_pred)


# --- teams ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TeamReport:
    n: int
    correct: int
    swapped: bool  # True if the best mapping was pred 0↔1

    @property
    def accuracy(self) -> float:
        return self.correct / self.n if self.n else float("nan")


def team_accuracy(pred: Sequence[int], gt: Sequence[int]) -> TeamReport:
    """Accuracy over items whose GT team is 0/1 (referees etc. = -1 are skipped), taking the better of
    the identity and swapped mappings — cluster ids are arbitrary per match. Evaluate ONE match per call."""
    p, g = np.asarray(pred), np.asarray(gt)
    if p.shape != g.shape:
        raise ValueError("pred and gt must have the same length")
    keep = (g == 0) | (g == 1)
    p, g = p[keep], g[keep]
    same = int(np.sum(p == g))
    swap = int(np.sum(((p == 0) & (g == 1)) | ((p == 1) & (g == 0))))
    return TeamReport(n=int(g.size), correct=max(same, swap), swapped=swap > same)


# --- shots ----------------------------------------------------------------------------------------

WIDE = "main_wide"
NON_WIDE = "non_wide"  # binary GT vocabulary (SP-09 labels): anything that is not main_wide


@dataclass(frozen=True)
class ShotReport:
    n: int
    correct: int
    tp: int  # predicted main_wide and truly main_wide
    fp: int
    fn: int

    @property
    def accuracy(self) -> float:
        return self.correct / self.n if self.n else float("nan")

    @property
    def wide_precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else float("nan")

    @property
    def wide_recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else float("nan")

    def __add__(self, o: ShotReport) -> ShotReport:
        return ShotReport(self.n + o.n, self.correct + o.correct, self.tp + o.tp, self.fp + o.fp, self.fn + o.fn)


EMPTY_SHOTS = ShotReport(0, 0, 0, 0, 0)


def shot_report(pred: Sequence[str | None], gt: Sequence[str | None]) -> ShotReport:
    """Compare shot labels sample by sample. ``None`` GT = unlabelled (skipped); ``None`` pred = wrong.
    If the GT uses the binary vocabulary (``non_wide``), predictions are collapsed to wide/non-wide."""
    if len(pred) != len(gt):
        raise ValueError("pred and gt must have the same length")
    n = correct = tp = fp = fn = 0
    for p, g in zip(pred, gt, strict=True):
        if g is None:
            continue
        if g == NON_WIDE and p is not None and p != WIDE:
            p = NON_WIDE
        n += 1
        correct += p == g
        tp += p == WIDE and g == WIDE
        fp += p == WIDE and g != WIDE
        fn += p != WIDE and g == WIDE
    return ShotReport(n, correct, tp, fp, fn)


Timeline = Sequence[tuple[int, int, str]]  # (t0_ms, t1_ms, type), sorted, contiguous


def label_at(timeline: Timeline, t_ms: int) -> str | None:
    for t0, t1, typ in timeline:
        if t0 <= t_ms < t1:
            return typ
    return None


def per_second_labels(timeline: Timeline, duration_s: int) -> list[str | None]:
    """Label of each second, sampled at its midpoint (t + 0.5 s)."""
    return [label_at(timeline, s * 1000 + 500) for s in range(duration_s)]


def shot_accuracy_per_second(pred: Timeline, gt: Timeline, duration_s: int) -> ShotReport:
    return shot_report(per_second_labels(pred, duration_s), per_second_labels(gt, duration_s))


# --- homography -----------------------------------------------------------------------------------


def project(H: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """Apply a 3x3 homography to (N,2) points."""
    pts = np.asarray(pts, float).reshape(-1, 2)
    hom = np.c_[pts, np.ones(len(pts))] @ np.asarray(H, float).T
    return hom[:, :2] / hom[:, 2:3]


def reprojection_errors(H: np.ndarray, src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """Euclidean error of each projected src point vs its dst point (pitch metres when dst is metres)."""
    return np.linalg.norm(project(H, src) - np.asarray(dst, float).reshape(-1, 2), axis=1)


@dataclass(frozen=True)
class HomographyReport:
    n_frames: int
    n_failed: int       # frames without a homography (count as above threshold)
    mean_error: float   # over frames with a homography
    median_error: float
    pct_within: float   # share of ALL frames with mean error ≤ threshold
    threshold: float


def homography_report(frame_errors: Sequence[float | None], threshold: float = 1.0) -> HomographyReport:
    """``frame_errors``: mean reprojection error per evaluated frame, ``None`` if no homography."""
    ok = np.array([e for e in frame_errors if e is not None], float)
    n = len(frame_errors)
    return HomographyReport(
        n_frames=n, n_failed=n - ok.size,
        mean_error=float(ok.mean()) if ok.size else float("nan"),
        median_error=float(np.median(ok)) if ok.size else float("nan"),
        pct_within=float((ok <= threshold).sum() / n) if n else float("nan"),
        threshold=threshold,
    )


# --- strata ---------------------------------------------------------------------------------------


@dataclass
class GroupTable:
    """Rows = group values of one stratum field, columns = metrics."""

    key: str
    rows: dict[str, dict[str, float]] = field(default_factory=dict)


def group_ids(strata: Mapping[str, Mapping[str, Any]], key: str) -> dict[str, list[str]]:
    """{stratum value: [match ids]}; missing/None values grouped as ``"unknown"``."""
    out: dict[str, list[str]] = defaultdict(list)
    for mid, s in strata.items():
        v = s.get(key)
        out["unknown" if v is None else str(v)].append(mid)
    return dict(sorted(out.items()))


def by_group(strata: Mapping[str, Mapping[str, Any]], keys: Sequence[str],
             metric: Callable[[list[str]], dict[str, float]]) -> list[GroupTable]:
    """Recompute ``metric(match_ids)`` on each group's pooled data (never a mean of means)."""
    return [GroupTable(k, {v: metric(ids) for v, ids in group_ids(strata, k).items()}) for k in keys]
