"""S3 calibration helpers: where to sample, which detections are players, and the sample-crop grid.

- frames are spread evenly over the *analysable* time (main_wide shots), at least ``margin_ms`` away from cuts
  (transition frames blend two shots); each frame is decoded on its own (seek + decode ≤ one 2 s GOP) — fine for
  ~50 frames, and no tracker runs here so per-frame seeking is harmless (SP-05's warning is about S4);
- a person box is a player candidate when its feet are on the grass mask and its height is 3–25 % of the frame
  (SP-04: excludes crowd / ads at the top and close-up giants); its torso crop feeds the team embedding.
"""
from __future__ import annotations

import math
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import cv2
import numpy as np

from cv_core.detect import COCO_PERSON, Detections
from cv_core.io.video import VideoReader
from cv_core.pitch.grass import grass_mask
from cv_core.teams.embed import torso_crop

Detector = Callable[[Sequence[np.ndarray]], list[Detections]]

MIN_PERSON_H, MAX_PERSON_H, MIN_TOP = 0.03, 0.25, 0.08  # fractions of the frame height (SP-04)


@dataclass(frozen=True, slots=True)
class CropSample:
    frame_idx: int
    box: tuple[int, int, int, int]   # person box xyxy on the normalized video
    score: float
    crop: np.ndarray                 # torso, BGR


def sample_frame_indices(intervals_ms: Sequence[tuple[int, int]], n: int, *, fps: float = 25.0,
                         margin_ms: int = 1000) -> list[int]:
    """``n`` frame indices spread evenly over the union of ``intervals_ms`` (each shrunk by ``margin_ms`` when it is
    long enough), deterministic, sorted, unique."""
    spans = []
    for t0, t1 in intervals_ms:
        m = margin_ms if t1 - t0 > 4 * margin_ms else 0
        if t1 - m > t0 + m:
            spans.append((t0 + m, t1 - m))
    total = sum(b - a for a, b in spans)
    if total <= 0 or n <= 0:
        return []
    out: list[int] = []
    for k in range(n):
        pos = (k + 0.5) * total / n
        for a, b in spans:
            if pos < b - a:
                out.append(math.floor((a + pos) * fps / 1000))
                break
            pos -= b - a
    return sorted(set(out))


def read_frames(src: str | os.PathLike[str], indices: Sequence[int], *, fps: float = 25.0
                ) -> list[tuple[int, np.ndarray]]:
    frames = []
    for idx in indices:
        for fr in VideoReader(src, idx / fps, (idx + 1) / fps, target_fps=fps):
            frames.append((fr.idx, fr.image))
    return frames


def player_candidates(image: np.ndarray, det: Detections) -> np.ndarray:
    """Boolean mask over ``det``: person, on the grass, plausible player size."""
    H = image.shape[0]
    b = det.boxes
    if len(b) == 0:
        return np.zeros(0, bool)
    h = b[:, 3] - b[:, 1]
    ok = (det.labels == COCO_PERSON) & (h > MIN_PERSON_H * H) & (h < MAX_PERSON_H * H) & (b[:, 1] > MIN_TOP * H)
    if ok.any():
        ok &= grass_mask(image).on_pitch(b)
    return np.asarray(ok)


def collect_crops(frames: Sequence[tuple[int, np.ndarray]], detector: Detector, *, batch: int = 8
                  ) -> list[CropSample]:
    out: list[CropSample] = []
    for s in range(0, len(frames), batch):
        chunk = frames[s:s + batch]
        dets = detector([im for _, im in chunk])
        for (idx, im), d in zip(chunk, dets, strict=True):
            keep = player_candidates(im, d)
            for box, score in zip(d.boxes[keep], d.scores[keep], strict=True):
                c = torso_crop(im, box)
                if c is not None:
                    bx = tuple(round(float(v)) for v in box)
                    out.append(CropSample(idx, (bx[0], bx[1], bx[2], bx[3]), float(score), c))
    return out


def crops_grid(rows: Sequence[tuple[str, Sequence[np.ndarray]]], *, tile: tuple[int, int] = (48, 64),
               per_row: int = 16) -> np.ndarray:
    """One labelled row of resized crops per entry (e.g. per cluster) → BGR image for eyeballing / the UI."""
    tw, th = tile
    label_w = 120
    lines = []
    for label, crops in rows:
        tiles = [cv2.resize(c, (tw, th), interpolation=cv2.INTER_NEAREST) for c in list(crops)[:per_row]]
        tiles += [np.zeros((th, tw, 3), np.uint8)] * (per_row - len(tiles))
        head = np.zeros((th, label_w, 3), np.uint8)
        cv2.putText(head, label[:14], (4, th // 2 + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1,
                    cv2.LINE_AA)
        lines.append(cv2.hconcat([head, *tiles]))
    return cv2.vconcat(lines) if lines else np.zeros((th, label_w, 3), np.uint8)
