"""Heatmap pitch-keypoint models (one heatmap per keypoint) on the cv_core Runtime.

Contract (``models/registry.yaml`` ``format: heatmap-world/v1``):
- input ``[1, 3, H, W]`` float32 RGB / 255, the frame **stretched** to ``W×H`` (no letterbox, no normalisation);
- output ``[1, K, H/s, W/s]`` heatmaps (``s`` = output stride, read from the shapes), one channel per keypoint;
- the entry lists each keypoint's pitch position ``world [K, [x, y] | null]`` in metres in the template frame
  (``null`` = not on the ground plane, e.g. a crossbar corner: never used for the homography).

Decoding: per channel the strongest peak (the 1-px border excluded, as edges are noisy) → source pixels.
``conf`` is the peak value rescaled so the model's own detection threshold maps to 0.5 (``HomographyConfig.min_conf``).
The first model of this kind is PnLCalib's single-view HRNet (research-only weights — see its model card).
"""
from __future__ import annotations

from collections.abc import Sequence

import cv2
import numpy as np

from cv_core.pitch.keypoints import PitchKeypoints
from cv_core.runtime import InferenceModel


def heatmap_peaks(hm: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """``[K, h, w]`` → (``xy [K, 2]`` heatmap px, ``score [K]``); border peaks score 0."""
    k, h, w = hm.shape
    flat = hm.reshape(k, -1)
    idx = flat.argmax(1)
    score = flat[np.arange(k), idx].astype(np.float32)
    ys, xs = np.divmod(idx, w)
    border = (xs == 0) | (ys == 0) | (xs == w - 1) | (ys == h - 1)
    score[border] = 0.0
    return np.stack([xs, ys], 1).astype(np.float32), score


def rescale_conf(score: np.ndarray, threshold: float) -> np.ndarray:
    """Piecewise-linear: 0 → 0, ``threshold`` → 0.5, 1 → 1."""
    s = np.clip(score, 0, 1)
    lo = s * 0.5 / threshold
    hi = 0.5 + 0.5 * (s - threshold) / (1 - threshold)
    return np.where(s < threshold, lo, hi).astype(np.float32)


class HeatmapPitchModel:
    """``kp = HeatmapPitchModel(runtime.model(path), world=entry["keypoints_world"]); per_frame = kp([bgr, ...])``."""

    def __init__(self, model: InferenceModel, *, world: Sequence[Sequence[float] | None], threshold: float = 0.5
                 ) -> None:  # fmt: skip
        self.model = model
        self.world = np.array([[np.nan, np.nan] if p is None else p for p in world], np.float64)
        self.threshold = threshold
        info = model.inputs[0]
        self.input_name, self.output_name = info.name, model.outputs[0].name
        h, w = info.shape[2], info.shape[3]
        if not (isinstance(h, int) and isinstance(w, int)):
            raise ValueError(f"model input {info.shape} must be static")
        self.size = (w, h)

    def __call__(self, images: Sequence[np.ndarray]) -> list[PitchKeypoints]:
        out = []
        for img in images:
            ih, iw = img.shape[:2]
            x = cv2.cvtColor(cv2.resize(img, self.size, interpolation=cv2.INTER_LINEAR), cv2.COLOR_BGR2RGB)
            x = (x.transpose(2, 0, 1)[None].astype(np.float32)) * np.float32(1 / 255)
            hm = self.model.run({self.input_name: x})[self.output_name][0]
            if hm.shape[0] != len(self.world):
                raise ValueError(f"model has {hm.shape[0]} keypoint channels, registry lists {len(self.world)}")
            xy, score = heatmap_peaks(hm)
            xy *= np.array([iw / hm.shape[2], ih / hm.shape[1]], np.float32)
            out.append(PitchKeypoints(xy, rescale_conf(score, self.threshold), np.array([0, 0, iw, ih], np.float32),
                                      float(score.max(initial=0.0)), (iw, ih), world=self.world))  # fmt: skip
        return out
