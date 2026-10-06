"""Synthetic broadcast-like clip for S3/S4 tests: green pitch, red / blue 'players', an orange 'referee', a ball.

``FakeDetector`` finds the coloured rectangles by colour (no model download); ``FakeEmbedder`` embeds a crop as its
mean colour (+ noise) — enough structure for the real PCA → KMeans → outlier → vote code paths.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import av
import cv2
import numpy as np
from cv_core.detect import COCO_PERSON, COCO_SPORTS_BALL, Detections

W, H, FPS = 640, 360, 25
PW, PH = 18, 48
RED, BLUE, ORANGE, WHITE = (40, 40, 220), (220, 60, 40), (0, 140, 255), (255, 255, 255)
GRASS = (40, 150, 40)
STAND = (90, 90, 90)  # grey band at the top: off-pitch


@dataclass(frozen=True)
class Mover:
    colour: tuple[int, int, int]
    x0: float
    y0: float
    vx: float
    vy: float

    def at(self, k: int) -> tuple[int, int]:
        x = self.x0 + self.vx * k
        y = self.y0 + self.vy * k
        return int(x), int(y)


def movers(n_per_team: int = 6) -> list[Mover]:
    """Non-overlapping lanes: red rows y=50/180, blue rows y=115/245, referee row y=310 (boxes 48 px tall)."""
    out = []
    for i in range(n_per_team):
        d = 0.15 if i % 2 else -0.15
        out.append(Mover(RED, 40 + 100 * i, 50 + 130 * (i % 2), d, 0.0))
        out.append(Mover(BLUE, 90 + 100 * i, 115 + 130 * (i % 2), -d, 0.0))
    out.append(Mover(ORANGE, 20, 310, 0.5, 0.0))
    return out


def frame(k: int, ms: list[Mover], *, grass: bool = True) -> np.ndarray:
    img = np.full((H, W, 3), GRASS if grass else (120, 40, 160), np.uint8)
    img[:40] = STAND
    for m in ms:
        x, y = m.at(k)
        img[y:y + PH, x:x + PW] = m.colour
    cv2.circle(img, (300 + k % 300, 334), 4, WHITE, -1)
    return img


def write_clip(path: Path, seconds: float = 8.0, *, grass: bool = True, same_colour: bool = False) -> Path:
    ms = movers()
    if same_colour:
        ms = [Mover(RED, m.x0, m.y0, m.vx, m.vy) for m in ms]
    with av.open(str(path), "w") as c:
        st = c.add_stream("libx264", rate=FPS)
        st.width, st.height, st.pix_fmt = W, H, "yuv420p"
        st.options = {"g": str(2 * FPS), "crf": "18", "preset": "ultrafast"}
        for k in range(int(seconds * FPS)):
            for p in st.encode(av.VideoFrame.from_ndarray(frame(k, ms, grass=grass), format="bgr24")):
                c.mux(p)
        for p in st.encode():
            c.mux(p)
    return path


class FakeDetector:
    """Colour-blob 'detector': players (red / blue / orange rectangles) and the white ball."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, images: Sequence[np.ndarray]) -> list[Detections]:
        out = []
        for img in images:
            self.calls += 1
            boxes, scores, labels = [], [], []
            for colour, label in ((RED, COCO_PERSON), (BLUE, COCO_PERSON), (ORANGE, COCO_PERSON),
                                  (WHITE, COCO_SPORTS_BALL)):
                lo = np.clip(np.array(colour) - 35, 0, 255).astype(np.uint8)
                hi = np.clip(np.array(colour) + 35, 0, 255).astype(np.uint8)
                m = cv2.inRange(img, lo, hi)
                n, _, stats, _ = cv2.connectedComponentsWithStats(m)
                for x, y, w, h, area in stats[1:n]:
                    if area < 8:
                        continue
                    boxes.append([x, y, x + w, y + h])
                    scores.append(0.9)
                    labels.append(label)
            out.append(Detections(np.array(boxes, np.float32).reshape(-1, 4), np.array(scores, np.float32),
                                  np.array(labels, np.int64)))
        return out


class FakeEmbedder:
    name = "fake-colour"

    def __init__(self) -> None:
        self.rng = np.random.default_rng(0)
        self.calls = 0

    def __call__(self, crops: Sequence[np.ndarray]) -> np.ndarray:
        self.calls += len(crops)
        if not crops:
            return np.zeros((0, 12), np.float32)
        mean = np.array([c.reshape(-1, 3).mean(0) / 255 for c in crops])
        e = np.c_[mean, mean**2, np.zeros((len(crops), 6))] + 0.01 * self.rng.normal(size=(len(crops), 12))
        e /= np.linalg.norm(e, axis=1, keepdims=True)
        return e.astype(np.float32)
