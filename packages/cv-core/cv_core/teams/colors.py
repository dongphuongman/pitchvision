"""Team display colours from torso crops (S3): per cluster, the median of the crops' mean shirt colour.

Grass-green pixels (the crop's background around a slim player) are left out of each crop's mean. The result is a
display default for the UI / tiles legend; the user confirms or changes it (``teams.confirmed``).
"""
from __future__ import annotations

from collections.abc import Sequence

import cv2
import numpy as np

GREEN_HUE = (35, 85)   # OpenCV hue range (0..180) treated as grass
MIN_SAT = 40


def crop_mean_bgr(crop: np.ndarray) -> np.ndarray | None:
    """Mean BGR of the non-grass pixels of a BGR uint8 crop (None for an empty / all-grass crop)."""
    if crop.size == 0:
        return None
    px = crop.reshape(-1, 3)
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV).reshape(-1, 3)
    grass = (hsv[:, 0] >= GREEN_HUE[0]) & (hsv[:, 0] <= GREEN_HUE[1]) & (hsv[:, 1] >= MIN_SAT)
    keep = px[~grass]
    if len(keep) < max(4, 0.1 * len(px)):
        return None
    return np.asarray(keep.mean(0), np.float64)


def to_hex(bgr: np.ndarray) -> str:
    b, g, r = (int(np.clip(round(float(v)), 0, 255)) for v in bgr)
    return f"#{r:02x}{g:02x}{b:02x}"


def cluster_colours(crops: Sequence[np.ndarray], labels: Sequence[int] | np.ndarray,
                    clusters: Sequence[int] = (0, 1)) -> dict[int, str]:
    """``{cluster: "#rrggbb"}`` for every cluster with at least one usable crop (outliers, label -1, ignored)."""
    lab = np.asarray(labels)
    out: dict[int, str] = {}
    for j in clusters:
        means = [m for c, lb in zip(crops, lab, strict=True) if lb == j and (m := crop_mean_bgr(c)) is not None]
        if means:
            out[j] = to_hex(np.median(np.stack(means), axis=0))
    return out
