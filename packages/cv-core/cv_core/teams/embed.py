"""Shirt-crop embeddings for the team split (S3/S4, SP-04): SigLIP-base vision tower on the cv_core Runtime.

Graph from ``training/export_siglip_onnx.py`` (registry ``siglip-base-p16-224``):
``pixel_values [N,3,224,224]`` (RGB, bicubic 224×224, (x/255−0.5)/0.5) → ``pooler_output [N,768]``.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

import cv2
import numpy as np

from cv_core.runtime import InferenceModel

SIGLIP_SIZE = 224
SIGLIP_MODEL = "siglip-base-p16-224"

# torso box inside a person box (fractions of w / h), SP-04: below the head, above the shorts, without the arms
TORSO_Y = (0.18, 0.55)
TORSO_X = (0.22, 0.78)
MIN_TORSO_PX = (8, 5)  # (h, w) — smaller crops carry no colour information


class Embedder(Protocol):
    """``embedder(crops) -> float32 [N, D]`` for BGR uint8 crops."""

    @property
    def name(self) -> str: ...

    def __call__(self, crops: Sequence[np.ndarray]) -> np.ndarray: ...


def torso_box(box: Sequence[float] | np.ndarray) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = (float(v) for v in box[:4])
    w, h = x2 - x1, y2 - y1
    return (int(x1 + TORSO_X[0] * w), int(y1 + TORSO_Y[0] * h), int(x1 + TORSO_X[1] * w), int(y1 + TORSO_Y[1] * h))


def torso_crop(image: np.ndarray, box: Sequence[float] | np.ndarray) -> np.ndarray | None:
    """Torso of a person box (copied, BGR) or None when it is too small / outside the image."""
    H, W = image.shape[:2]
    x1, y1, x2, y2 = torso_box(box)
    x1, y1, x2, y2 = max(0, x1), max(0, y1), min(W, x2), min(H, y2)
    if y2 - y1 < MIN_TORSO_PX[0] or x2 - x1 < MIN_TORSO_PX[1]:
        return None
    return image[y1:y2, x1:x2].copy()


def preprocess(crops: Sequence[np.ndarray], size: int = SIGLIP_SIZE) -> np.ndarray:
    """BGR uint8 crops → float32 [N, 3, size, size] like ``SiglipImageProcessor`` (bicubic, mean = std = 0.5)."""
    out = np.empty((len(crops), 3, size, size), dtype=np.float32)
    for i, c in enumerate(crops):
        interp = cv2.INTER_AREA if c.shape[0] > size or c.shape[1] > size else cv2.INTER_CUBIC
        rgb = cv2.cvtColor(cv2.resize(c, (size, size), interpolation=interp), cv2.COLOR_BGR2RGB)
        out[i] = rgb.transpose(2, 0, 1)
    out *= np.float32(2 / 255)
    out -= np.float32(1)
    return out


class SiglipEmbedder:
    """``emb = SiglipEmbedder(runtime.model(path))(crops)`` → float32 [N, 768], L2-normalised by default."""

    def __init__(self, model: InferenceModel, *, batch: int = 32, normalize: bool = True,
                 name: str = SIGLIP_MODEL) -> None:
        self.model = model
        self.input_name = model.inputs[0].name
        b = model.inputs[0].shape[0]
        self.batch = b if isinstance(b, int) and b > 0 else batch
        self.normalize = normalize
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def __call__(self, crops: Sequence[np.ndarray]) -> np.ndarray:
        parts = []
        for s in range(0, len(crops), self.batch):
            res = self.model.run({self.input_name: preprocess(crops[s:s + self.batch])})
            parts.append(next(iter(res.values())).astype(np.float32))
        if not parts:
            return np.zeros((0, 0), np.float32)
        emb = np.concatenate(parts)
        if self.normalize:
            emb /= np.linalg.norm(emb, axis=1, keepdims=True) + 1e-12
        return emb
