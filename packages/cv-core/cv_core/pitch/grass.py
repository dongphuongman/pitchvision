"""Pitch (grass) mask and the on-pitch test for detections (S3/S4 v1 off-pitch filter, SP-11).

Pitch = largest HSV-green blob, morphologically closed so players / lines inside it count as pitch. Computed on a
downscaled image (``scale``) — the blob is huge, so 1/4 resolution is plenty and ~15× cheaper than full 1280×720.
A detection is on the pitch when its bottom-centre (feet / ball contact point, 2 px above the box edge) is inside.
Replaced by the homography (pitch polygon) in W4 where available.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

GRASS_HSV_LO = (30, 40, 40)
GRASS_HSV_HI = (90, 255, 255)


@dataclass(frozen=True, slots=True)
class PitchMask:
    mask: np.ndarray   # bool [h, w] at ``scale`` of the image
    scale: float       # mask px per image px

    @property
    def coverage(self) -> float:
        return float(self.mask.mean()) if self.mask.size else 0.0

    def contains(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        """Image-pixel points → bool (outside the image → False)."""
        h, w = self.mask.shape
        xs = np.floor(np.asarray(x, np.float64) * self.scale).astype(np.int64)
        ys = np.floor(np.asarray(y, np.float64) * self.scale).astype(np.int64)
        inside = (xs >= 0) & (xs < w) & (ys >= 0) & (ys < h)
        out = np.zeros(xs.shape, bool)
        out[inside] = self.mask[ys[inside], xs[inside]]
        return out

    def on_pitch(self, boxes: np.ndarray) -> np.ndarray:
        """xyxy boxes [N, 4] (image px) → bool [N]: bottom-centre on the pitch."""
        b = np.asarray(boxes, np.float64).reshape(-1, 4)
        return self.contains((b[:, 0] + b[:, 2]) / 2, b[:, 3] - 2)


def grass_mask(image: np.ndarray, *, scale: float = 0.25, close_px: int = 31) -> PitchMask:
    """BGR image → ``PitchMask`` (largest closed green component). ``close_px`` is in full-resolution pixels."""
    small = image if scale == 1 else cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, GRASS_HSV_LO, GRASS_HSV_HI)
    k = max(3, round(close_px * scale) | 1)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m)
    mask = (lab == 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))) if n > 1 else m > 0
    return PitchMask(mask=mask, scale=scale)
