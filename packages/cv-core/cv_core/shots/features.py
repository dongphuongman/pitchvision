"""Per-frame shot features (SP-09 port): pitch colour / line / edge statistics + person count & size.

Everything is computed on the frame resized to ``ANALYSIS_LONG_SIDE`` (640) so the features do not depend on the
source resolution (normalized.mp4 is 1280, SP-09 JPEGs are 640, broadcast originals up to 4K).

Feature vector (order is part of the model contract — the model file stores the names and is checked on load):

colour (always): ``green, lower_green, top_green, lines, edges, sat_mean, val_mean, val_std, hue_green_std``
person (needs a detector): ``n_person, h_max, h_median, h_mean, n_small`` — heights relative to frame height,
persons = COCO class 0 at ``conf`` ≥ detector threshold; ``n_small`` = persons shorter than 10 % of the frame.
"""
from __future__ import annotations

import cv2
import numpy as np

from cv_core.detect.dfine import COCO_PERSON, Detections

ANALYSIS_LONG_SIDE = 640
COLOUR_FEATURES: tuple[str, ...] = ("green", "lower_green", "top_green", "lines", "edges", "sat_mean", "val_mean",
                                    "val_std", "hue_green_std")
PERSON_FEATURES: tuple[str, ...] = ("n_person", "h_max", "h_median", "h_mean", "n_small")
SMALL_PERSON_H = 0.10

_KERNEL9 = np.ones((9, 9), np.uint8)


def analysis_image(bgr: np.ndarray, long_side: int = ANALYSIS_LONG_SIDE) -> np.ndarray:
    h, w = bgr.shape[:2]
    if max(h, w) == long_side:
        return bgr
    s = long_side / max(h, w)
    interp = cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR
    return cv2.resize(bgr, (max(1, round(w * s)), max(1, round(h * s))), interpolation=interp)


def colour_features(bgr: np.ndarray) -> np.ndarray:
    """SP-09 colour features (+ a few colour statistics) → float32 [len(COLOUR_FEATURES)]."""
    img = analysis_image(bgr)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, (30, 40, 40), (90, 255, 255)) > 0
    white = cv2.inRange(hsv, (0, 0, 170), (180, 60, 255)) > 0
    on_pitch = cv2.dilate(green.astype(np.uint8), _KERNEL9) > 0
    edges = cv2.Canny(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), 80, 160)
    h3 = green.shape[0] // 3
    hue = hsv[..., 0]
    hue_green = hue[green]
    return np.array([
        green.mean(),
        green[h3:].mean(),
        green[:h3].mean(),
        (white & on_pitch).mean(),
        edges.mean() / 255,
        hsv[..., 1].mean() / 255,
        hsv[..., 2].mean() / 255,
        hsv[..., 2].std() / 255,
        hue_green.std() / 90 if hue_green.size > 16 else 0.0,
    ], dtype=np.float32)


def person_features(dets: Detections, frame_h: int) -> np.ndarray:
    """Person count / size statistics from one frame's detections → float32 [len(PERSON_FEATURES)]."""
    p = dets.of_class(COCO_PERSON)
    if len(p) == 0:
        return np.zeros(len(PERSON_FEATURES), dtype=np.float32)
    hs = (p.boxes[:, 3] - p.boxes[:, 1]) / float(frame_h)
    return np.array([len(p), hs.max(), np.median(hs), hs.mean(), (hs < SMALL_PERSON_H).sum()], dtype=np.float32)


def feature_names(with_persons: bool) -> tuple[str, ...]:
    return COLOUR_FEATURES + PERSON_FEATURES if with_persons else COLOUR_FEATURES
