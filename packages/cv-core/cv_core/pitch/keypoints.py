"""Pitch keypoint model on the cv_core Runtime: a YOLO-pose ONNX graph (``training/keypoints/export_onnx.py``).

Ultralytics stays out of cv-core (AGPL-3.0 without the Enterprise licence): this module only decodes the exported
graph. Contract (Ultralytics ``format=onnx``, ``nms=False``):

- input ``images [1, 3, H, W]`` float32 RGB / 255, **letterboxed**: resize by ``r = min(H/h, W/w)`` (aspect kept),
  centred padding with grey 114 — Ultralytics ``LetterBox(auto=False, center=True)``; ``H, W`` are read from the
  graph (fixed input, e.g. 640×640 or 384×640);
- output ``output0 [1, 4 + nc + 3K, A]``: per anchor ``cx, cy, w, h`` (input px), ``nc`` class scores (sigmoid
  applied in-graph), then K × ``(x, y, conf)`` with x/y in input px and conf already sigmoid'ed.

An end-to-end export (``nms=True``) giving ``[1, D, 6 + 3K]`` (``x1, y1, x2, y2, score, cls, kpts…``) is decoded too.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import cv2
import numpy as np

from cv_core.pitch.template import NUM_KEYPOINTS
from cv_core.runtime import InferenceModel

LETTERBOX_FILL = 114


@dataclass(frozen=True, slots=True)
class Letterbox:
    """Mapping between source pixels and model-input pixels: ``input = src * scale + (pad_x, pad_y)``."""

    scale: float
    pad_x: float
    pad_y: float
    src_size: tuple[int, int]  # (w, h)

    def to_source(self, xy: np.ndarray) -> np.ndarray:
        out = np.asarray(xy, np.float32).copy()
        out[..., 0] = (out[..., 0] - self.pad_x) / self.scale
        out[..., 1] = (out[..., 1] - self.pad_y) / self.scale
        return out


def letterbox(img: np.ndarray, size: tuple[int, int]) -> tuple[np.ndarray, Letterbox]:
    """BGR uint8 HxWx3 → (RGB float32 [3, H, W] / 255, Letterbox); ``size`` = (H, W) of the model input.

    Same arithmetic as Ultralytics ``LetterBox(auto=False, scaleup=True, center=True)``: new size =
    ``round(w*r), round(h*r)``, padding split ``round(d/2 ∓ 0.1)``, cv2 INTER_LINEAR resize."""
    H, W = size
    h, w = img.shape[:2]
    r = min(H / h, W / w)
    nw, nh = round(w * r), round(h * r)
    dw, dh = (W - nw) / 2, (H - nh) / 2
    top, bottom = round(dh - 0.1), round(dh + 0.1)
    left, right = round(dw - 0.1), round(dw + 0.1)
    resized = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR) if (nw, nh) != (w, h) else img
    padded = cv2.copyMakeBorder(resized, top, bottom, left, right, cv2.BORDER_CONSTANT,
                                value=(LETTERBOX_FILL,) * 3)
    rgb = cv2.cvtColor(padded, cv2.COLOR_BGR2RGB).transpose(2, 0, 1).astype(np.float32)
    rgb *= np.float32(1 / 255)
    return rgb, Letterbox(r, float(left), float(top), (w, h))


@dataclass(frozen=True, slots=True)
class PitchKeypoints:
    """One pitch instance: ``xy [K, 2]`` source px, ``conf [K]`` 0..1 (visibility × localisation confidence)."""

    xy: np.ndarray
    conf: np.ndarray
    box: np.ndarray        # [4] xyxy source px
    score: float           # instance (class) score
    image_size: tuple[int, int]  # (w, h) of the source frame

    @property
    def num_keypoints(self) -> int:
        return len(self.conf)

    @classmethod
    def empty(cls, image_size: tuple[int, int], k: int = NUM_KEYPOINTS) -> PitchKeypoints:
        return cls(np.zeros((k, 2), np.float32), np.zeros(k, np.float32), np.zeros(4, np.float32), 0.0, image_size)


def _nms(boxes_xyxy: np.ndarray, scores: np.ndarray, iou: float) -> np.ndarray:
    if len(scores) == 0:
        return np.zeros(0, np.int64)
    xywh = np.c_[boxes_xyxy[:, :2], boxes_xyxy[:, 2:] - boxes_xyxy[:, :2]].astype(np.float32)
    keep = cv2.dnn.NMSBoxes(xywh.tolist(), scores.astype(np.float32).tolist(), 0.0, iou)
    return np.asarray(keep, np.int64).reshape(-1)


def decode(output: np.ndarray, lb: Letterbox, *, num_keypoints: int = NUM_KEYPOINTS, conf: float = 0.25,
           iou: float = 0.7, max_det: int = 5) -> list[PitchKeypoints]:
    """One image's raw output (``[C, A]`` raw head, or ``[D, 6+3K]`` end-to-end) → instances by score, NMS'd."""
    k3 = 3 * num_keypoints
    out = np.asarray(output, np.float32)
    if out.ndim == 3:
        out = out[0]
    if out.shape[-1] == 6 + k3 and out.shape[0] != 6 + k3:  # end-to-end (NMS inside the graph)
        boxes, scores, kpts = out[:, :4], out[:, 4], out[:, 6:]
        sel = scores > conf
        boxes, scores, kpts = boxes[sel], scores[sel], kpts[sel]
        order = np.argsort(-scores)[:max_det]
    else:
        if 5 + k3 <= out.shape[0] <= 4 + k3 + 1000 and not 5 + k3 <= out.shape[1] <= 4 + k3 + 1000:
            out = out.T  # Ultralytics layout [C, A] → [A, C]
        nc = out.shape[1] - 4 - k3
        if nc < 1:
            raise ValueError(f"output has {out.shape[1]} channels; expected 4 + nc + {k3} for {num_keypoints} kpts")
        cls = out[:, 4:4 + nc]
        scores = cls.max(axis=1)
        sel = scores > conf
        cxcywh, scores, kpts = out[sel, :4], scores[sel], out[sel, 4 + nc:]
        boxes = np.c_[cxcywh[:, :2] - cxcywh[:, 2:] / 2, cxcywh[:, :2] + cxcywh[:, 2:] / 2]
        keep = _nms(boxes, scores, iou)
        order = keep[np.argsort(-scores[keep])][:max_det]
    res = []
    for i in order:
        kp = kpts[i].reshape(num_keypoints, 3)
        b = lb.to_source(boxes[i].reshape(2, 2)).reshape(4)
        res.append(PitchKeypoints(lb.to_source(kp[:, :2]), np.clip(kp[:, 2], 0, 1).astype(np.float32), b,
                                  float(scores[i]), lb.src_size))
    return res


class PitchKeypointModel:
    """``kp = PitchKeypointModel(runtime.model("pitch_keypoints")); per_frame = kp([bgr, ...])``.

    Returns the best pitch instance per frame (``PitchKeypoints.empty`` when nothing passes ``conf``)."""

    def __init__(self, model: InferenceModel, *, num_keypoints: int = NUM_KEYPOINTS, conf: float = 0.25,
                 iou: float = 0.7, imgsz: tuple[int, int] | None = None) -> None:
        self.model = model
        self.num_keypoints = num_keypoints
        self.conf = conf
        self.iou = iou
        info = model.inputs[0]
        self.input_name = info.name
        h, w = info.shape[2], info.shape[3]
        if imgsz is None:
            if not (isinstance(h, int) and isinstance(w, int) and h > 0 and w > 0):
                raise ValueError(f"model input {info.shape} is dynamic; pass imgsz=(H, W)")
            imgsz = (h, w)
        self.imgsz = imgsz
        b = info.shape[0]
        self.max_batch = b if isinstance(b, int) and b > 0 else 8
        self.output_name = model.outputs[0].name

    def instances(self, images: Sequence[np.ndarray]) -> list[list[PitchKeypoints]]:
        out: list[list[PitchKeypoints]] = []
        for s in range(0, len(images), self.max_batch):
            chunk = images[s:s + self.max_batch]
            pre = [letterbox(im, self.imgsz) for im in chunk]
            batch = np.stack([p[0] for p in pre])
            raw = self.model.run({self.input_name: batch})[self.output_name]
            for i, (_, lb) in enumerate(pre):
                out.append(decode(raw[i], lb, num_keypoints=self.num_keypoints, conf=self.conf, iou=self.iou))
        return out

    def __call__(self, images: Sequence[np.ndarray]) -> list[PitchKeypoints]:
        res = []
        for im, inst in zip(images, self.instances(images), strict=True):
            res.append(inst[0] if inst else PitchKeypoints.empty((im.shape[1], im.shape[0]), self.num_keypoints))
        return res
