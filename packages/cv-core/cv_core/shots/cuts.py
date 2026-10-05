"""Hard-cut detection by HSV histogram difference (basic S2b boundary detector; full S2b = M1-CV-08).

Per frame: 2-D hue × saturation histogram (+ a value histogram) of a small thumbnail; distance to the previous
frame = Bhattacharyya. A cut is declared when the distance is both large in absolute terms (``threshold``) and
large relative to the recent motion level (``ratio`` × median of the last ``window`` distances) — fast pans and
zooms of the main camera raise the median, so they do not fire, while a cut is a one-frame spike. A minimum gap
between cuts (``min_gap_frames``) suppresses double fires on flashes / dissolves. Gradual transitions (wipes,
replay logos) are not cuts here; the timeline builder splits on persistent class changes instead.
"""
from __future__ import annotations

from collections import deque

import cv2
import numpy as np

THUMB_W = 160


def frame_histogram(bgr: np.ndarray) -> np.ndarray:
    h, w = bgr.shape[:2]
    if w != THUMB_W:
        bgr = cv2.resize(bgr, (THUMB_W, max(1, round(h * THUMB_W / w))), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    hs = cv2.calcHist([hsv], [0, 1], None, [16, 8], [0, 180, 0, 256]).ravel()
    v = cv2.calcHist([hsv], [2], None, [16], [0, 256]).ravel()
    hist = np.concatenate([hs / max(hs.sum(), 1.0), v / max(v.sum(), 1.0)]).astype(np.float32)
    return hist


def hist_distance(a: np.ndarray, b: np.ndarray) -> float:
    """Mean Bhattacharyya distance of the HS and V parts (0 = identical, 1 = disjoint)."""
    d_hs = cv2.compareHist(a[:128], b[:128], cv2.HISTCMP_BHATTACHARYYA)
    d_v = cv2.compareHist(a[128:], b[128:], cv2.HISTCMP_BHATTACHARYYA)
    return float(0.5 * (d_hs + d_v))


class CutDetector:
    """Feed frames in order with ``update(idx, bgr)``; returns True when frame ``idx`` starts a new shot."""

    def __init__(self, threshold: float = 0.30, ratio: float = 3.0, window: int = 25, min_gap_frames: int = 10) -> None:
        self.threshold = threshold
        self.ratio = ratio
        self.min_gap = min_gap_frames
        self._recent: deque[float] = deque(maxlen=window)
        self._prev: np.ndarray | None = None
        self._last_cut = -(10**9)
        self.distances: list[tuple[int, float]] = []

    def update(self, idx: int, bgr: np.ndarray) -> bool:
        h = frame_histogram(bgr)
        prev, self._prev = self._prev, h
        if prev is None:
            return False
        d = hist_distance(prev, h)
        self.distances.append((idx, d))
        base = float(np.median(self._recent)) if self._recent else 0.0
        self._recent.append(d)
        if d >= self.threshold and d >= self.ratio * base and idx - self._last_cut >= self.min_gap:
            self._last_cut = idx
            return True
        return False
