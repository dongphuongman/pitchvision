"""D-FINE detector on the cv_core Runtime (ONNX graph from ``training/export_dfine_onnx.py``).

Graph: ``pixel_values [N,3,S,S]`` (RGB, stretched to S×S, /255) → ``logits [N,Q,C]`` + ``pred_boxes [N,Q,4]``
(cx, cy, w, h in 0..1). Post-processing mirrors ``RTDetrImageProcessor.post_process_object_detection``
with ``use_focal_loss=True``: sigmoid, top-Q over queries×classes, cxcywh → xyxy in source pixels,
then the score threshold. D-FINE is Apache-2.0 (G-LEGAL 05/10/2026, SP-11).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import cv2
import numpy as np

from cv_core.runtime import InferenceModel

COCO_PERSON = 0
COCO_SPORTS_BALL = 32


@dataclass(frozen=True, slots=True)
class Detections:
    boxes: np.ndarray   # float32 [N, 4] xyxy, source-image pixels
    scores: np.ndarray  # float32 [N]
    labels: np.ndarray  # int64 [N] model class ids (COCO 0-based for obj2coco)

    def __len__(self) -> int:
        return len(self.scores)

    def select(self, mask: np.ndarray) -> Detections:
        return Detections(self.boxes[mask], self.scores[mask], self.labels[mask])

    def of_class(self, *labels: int) -> Detections:
        return self.select(np.isin(self.labels, labels))


def preprocess(images: Sequence[np.ndarray], imgsz: int) -> np.ndarray:
    """BGR uint8 HxWx3 images → float32 [N, 3, imgsz, imgsz] RGB / 255, stretched (no letterbox) like the HF
    processor. Downscaling uses INTER_AREA: PIL's bilinear (HF) antialiases, cv2's INTER_LINEAR does not."""
    out = np.empty((len(images), 3, imgsz, imgsz), dtype=np.float32)
    for i, img in enumerate(images):
        down = img.shape[0] > imgsz or img.shape[1] > imgsz
        interp = cv2.INTER_AREA if down else cv2.INTER_LINEAR
        rgb = cv2.cvtColor(cv2.resize(img, (imgsz, imgsz), interpolation=interp), cv2.COLOR_BGR2RGB)
        out[i] = rgb.transpose(2, 0, 1)
    out *= np.float32(1 / 255)
    return out


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def postprocess(logits: np.ndarray, pred_boxes: np.ndarray, sizes: Sequence[tuple[int, int]],
                conf: float, top_k: int | None = None) -> list[Detections]:
    """Raw graph outputs → per-image ``Detections`` (``sizes`` = (h, w) of each source image)."""
    n, q, c = logits.shape
    k = min(top_k or q, q * c)
    scores_all = _sigmoid(logits.astype(np.float32)).reshape(n, q * c)
    out: list[Detections] = []
    for i in range(n):
        s = scores_all[i]
        idx = np.argpartition(-s, k - 1)[:k] if k < s.size else np.arange(s.size)
        idx = idx[np.argsort(-s[idx], kind="stable")]
        sc = s[idx]
        keep = sc > conf
        idx, sc = idx[keep], sc[keep]
        lab = (idx % c).astype(np.int64)
        b = pred_boxes[i, idx // c].astype(np.float32)
        h, w = sizes[i]
        xyxy = np.empty_like(b)
        xyxy[:, 0] = (b[:, 0] - b[:, 2] / 2) * w
        xyxy[:, 1] = (b[:, 1] - b[:, 3] / 2) * h
        xyxy[:, 2] = (b[:, 0] + b[:, 2] / 2) * w
        xyxy[:, 3] = (b[:, 1] + b[:, 3] / 2) * h
        out.append(Detections(xyxy, sc.astype(np.float32), lab))
    return out


class DFineDetector:
    """``det = DFineDetector(runtime.model("dfine-m-obj2coco-640")); dets = det([bgr, ...])``."""

    def __init__(self, model: InferenceModel, *, imgsz: int = 640, conf: float = 0.35,
                 max_batch: int = 8) -> None:
        self.model = model
        self.imgsz = imgsz
        self.conf = conf
        self.input_name = model.inputs[0].name
        b = model.inputs[0].shape[0]
        self.max_batch = b if isinstance(b, int) and b > 0 else max_batch

    def __call__(self, images: Sequence[np.ndarray]) -> list[Detections]:
        out: list[Detections] = []
        for s in range(0, len(images), self.max_batch):
            chunk = images[s:s + self.max_batch]
            res = self.model.run({self.input_name: preprocess(chunk, self.imgsz)})
            out += postprocess(res["logits"], res["pred_boxes"], [im.shape[:2] for im in chunk], self.conf)
        return out
