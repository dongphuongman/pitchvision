"""SP-09 shot labels → eval format, and a trivial green-ratio baseline classifier.

SP-09 sampled one frame every 20 s from t = 5 s of each 15-min golden segment (index.json: video, t, k,
file) and listed, per video, the k that are NOT main wide-angle play (labels_nonwide.json).
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from pveval.formats import ShotSample
from pveval.metrics import NON_WIDE, WIDE

LOWER_GREEN_MIN = 0.55  # SP-09 rule threshold on the grass share of the lower 2/3 of the frame


def sp09_samples(sp09_dir: Path) -> tuple[list[ShotSample], dict[tuple[str, int], str]]:
    """GT samples (binary main_wide / non_wide) + {(video, t_ms): frame file}."""
    index = json.loads((sp09_dir / "index.json").read_text())
    nonwide: dict[str, Any] = json.loads((sp09_dir / "labels_nonwide.json").read_text())
    samples, files = [], {}
    for it in index:
        if it["video"] not in nonwide:
            continue  # unlabelled video
        label = NON_WIDE if it["k"] in nonwide[it["video"]] else WIDE
        s = ShotSample(it["video"], int(it["t"]) * 1000, label)
        samples.append(s)
        files[(s.video, s.t_ms)] = it["file"]
    return samples, files


def colour_features(bgr: np.ndarray) -> dict[str, float]:
    """SP-09 colour features: grass share (all / lower 2/3), pitch-line share, edge density."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, (30, 40, 40), (90, 255, 255)) > 0
    white = cv2.inRange(hsv, (0, 0, 170), (180, 60, 255)) > 0
    on_pitch = cv2.dilate(green.astype(np.uint8), np.ones((9, 9), np.uint8)) > 0
    edges = cv2.Canny(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), 80, 160)
    return {
        "green": float(green.mean()),
        "lower_green": float(green[green.shape[0] // 3:].mean()),
        "lines": float((white & on_pitch).mean()),
        "edges": float(edges.mean() / 255),
    }


def green_rule(feat: dict[str, float], thr: float = LOWER_GREEN_MIN) -> tuple[str, float]:
    """Trivial baseline: lots of grass in the lower 2/3 → main_wide. Returns (label, pseudo-confidence)."""
    g = feat["lower_green"]
    return (WIDE if g > thr else NON_WIDE), float(min(1.0, abs(g - thr) / max(thr, 1 - thr) + 0.5))


def frames_from_jpgs(sp09_dir: Path, files: dict[tuple[str, int], str],
                     at: list[ShotSample]) -> Iterator[tuple[ShotSample, np.ndarray | None]]:
    for s in at:
        img = cv2.imread(str(sp09_dir / "frames" / files[(s.video, s.t_ms)]))
        yield s, img


def frames_from_videos(golden_dir: Path, at: list[ShotSample],
                       long_side: int = 640) -> Iterator[tuple[ShotSample, np.ndarray | None]]:
    """Decode each sample with cv_core.VideoReader on the normalised 25 fps timeline (one seek per sample)."""
    from cv_core.io import VideoReader

    for s in at:
        path = golden_dir / "raw" / f"{s.video}.mp4"
        if not path.is_file():
            yield s, None
            continue
        t = s.t_ms / 1000
        frame = next(iter(VideoReader(path, t, t + 0.04, target_fps=25, long_side=long_side)), None)
        yield s, frame.image if frame is not None else None
