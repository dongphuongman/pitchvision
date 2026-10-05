"""Gradient-boosted trees evaluated in pure numpy (no scikit-learn at runtime).

Why not skl2onnx / pickle: a pickle ties the worker to the exact sklearn version and is unsafe to load; skl2onnx
adds a converter dependency and an opaque graph for ~600 tiny trees. The model file is a plain JSON
(``pv-gbm/v1``) written by ``training/train_shots.py`` from a fitted ``HistGradientBoostingClassifier``; evaluation is
~40 lines of vectorised numpy and is checked bit-for-bit (≤ 1e-6) against ``predict_proba`` at export time.

Format::

    {"format": "pv-gbm/v1", "schema_version": 1, "name": ..., "version": ...,
     "classes": ["main_wide", "non_wide"],             # model outputs, in order
     "class_map": {"non_wide": "other"},               # → pitchvision_schemas.ShotType (identity if absent)
     "features": [...], "detector": "dfine-m-obj2coco-640" | null,
     "baseline": [b_0, ...],                            # 1 value (binary, logit of classes[1]) or K (softmax)
     "trees": [[{"f": [...], "t": [...], "l": [...], "r": [...], "v": [...], "nan_left": [...]}, ...K], ...],
     "meta": {...}}

Node ``i`` is a leaf when ``l[i] == -1``; otherwise ``x[f[i]] <= t[i]`` (or NaN and ``nan_left[i]``) goes to ``l[i]``.
Leaf values already include the learning rate (HistGradientBoosting convention).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

FORMAT = "pv-gbm/v1"
SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class Tree:
    feature: np.ndarray    # int32 [nodes]
    threshold: np.ndarray  # float64 [nodes]
    left: np.ndarray       # int32 [nodes], -1 = leaf
    right: np.ndarray      # int32 [nodes]
    value: np.ndarray      # float64 [nodes] (leaf output)
    nan_left: np.ndarray   # bool [nodes]

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Tree:
        n = len(d["v"])
        return cls(np.asarray(d["f"], np.int32), np.asarray(d["t"], np.float64), np.asarray(d["l"], np.int32),
                   np.asarray(d["r"], np.int32), np.asarray(d["v"], np.float64),
                   np.asarray(d.get("nan_left", [True] * n), bool))

    def to_json(self) -> dict[str, Any]:
        return {"f": self.feature.tolist(), "t": self.threshold.tolist(), "l": self.left.tolist(),
                "r": self.right.tolist(), "v": self.value.tolist(), "nan_left": self.nan_left.tolist()}

    def predict(self, X: np.ndarray) -> np.ndarray:
        node = np.zeros(len(X), dtype=np.int32)
        rows = np.arange(len(X))
        for _ in range(len(self.value)):  # bounded by node count; exits at max depth
            inner = self.left[node] >= 0
            if not inner.any():
                break
            n = node[inner]
            x = X[rows[inner], self.feature[n]]
            go_left = np.where(np.isnan(x), self.nan_left[n], x <= self.threshold[n])
            node[inner] = np.where(go_left, self.left[n], self.right[n])
        return self.value[node]


@dataclass(frozen=True)
class GbmModel:
    name: str
    version: str
    classes: tuple[str, ...]
    features: tuple[str, ...]
    baseline: np.ndarray
    trees: tuple[tuple[Tree, ...], ...]
    class_map: dict[str, str] = field(default_factory=dict)
    detector: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def binary(self) -> bool:
        return len(self.baseline) == 1

    def raw(self, X: np.ndarray) -> np.ndarray:
        X = np.asarray(X, dtype=np.float64)
        if X.ndim != 2 or X.shape[1] != len(self.features):
            raise ValueError(f"expected [n, {len(self.features)}] features, got {X.shape}")
        out = np.tile(self.baseline, (len(X), 1))
        for it in self.trees:
            for k, tree in enumerate(it):
                out[:, k] += tree.predict(X)
        return out

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """[n, len(classes)] probabilities (binary: sigmoid of the logit of classes[1])."""
        r = self.raw(X)
        if self.binary:
            p1 = 1.0 / (1.0 + np.exp(-r[:, 0]))
            return np.column_stack([1.0 - p1, p1])
        e = np.exp(r - r.max(axis=1, keepdims=True))
        return np.asarray(e / e.sum(axis=1, keepdims=True))

    def to_json(self) -> dict[str, Any]:
        return {"format": FORMAT, "schema_version": SCHEMA_VERSION, "name": self.name, "version": self.version,
                "classes": list(self.classes), "class_map": self.class_map, "features": list(self.features),
                "detector": self.detector, "baseline": self.baseline.tolist(),
                "trees": [[t.to_json() for t in it] for it in self.trees], "meta": self.meta}

    def save(self, path: str | os.PathLike[str]) -> None:
        Path(path).write_text(json.dumps(self.to_json(), separators=(",", ":")))

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> GbmModel:
        if d.get("format") != FORMAT:
            raise ValueError(f"not a {FORMAT} model (format={d.get('format')!r})")
        if int(d.get("schema_version", 0)) != SCHEMA_VERSION:
            raise ValueError(f"unsupported {FORMAT} schema_version {d.get('schema_version')}")
        classes = tuple(d["classes"])
        baseline = np.asarray(d["baseline"], np.float64).reshape(-1)
        trees = tuple(tuple(Tree.from_json(t) for t in it) for it in d["trees"])
        k = 1 if len(classes) == 2 else len(classes)
        if len(baseline) != k or any(len(it) != k for it in trees):
            raise ValueError(f"{len(classes)} classes need {k} trees per iteration")
        return cls(name=d.get("name", ""), version=str(d.get("version", "")), classes=classes,
                   features=tuple(d["features"]), baseline=baseline, trees=trees,
                   class_map=dict(d.get("class_map") or {}), detector=d.get("detector"), meta=d.get("meta") or {})

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> GbmModel:
        return cls.from_json(json.loads(Path(path).read_text()))
