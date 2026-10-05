"""Frame → shot-type probabilities (S2b): features + GBM, optional D-FINE person features.

4-class ready: the model file declares its classes and a ``class_map`` onto ``ShotType``. The M1 model is binary
(``main_wide`` vs ``non_wide`` → ``other``; SP-09 labels). A 4-class model (``main_wide, close_up, replay, other``,
trained on SoccerNet camera-shot + our labels, see training/train_shots.py) drops in without code changes.
Note ``replay`` is mostly a *temporal* property (replay logos/wipes, slow motion) — a per-frame model will only
partly get it; M2 adds replay detection on shot sequences (AD §6, v1.2 notes).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from pitchvision_schemas import ShotType

from cv_core.detect.dfine import DFineDetector
from cv_core.shots.features import COLOUR_FEATURES, PERSON_FEATURES, colour_features, person_features
from cv_core.shots.gbm import GbmModel

SHOT_TYPES: tuple[ShotType, ...] = tuple(ShotType)


@dataclass(frozen=True, slots=True)
class ShotProbs:
    """P(type) over ``SHOT_TYPES`` order (main_wide, close_up, replay, other) for one frame."""

    probs: np.ndarray  # float64 [4]

    @property
    def type(self) -> ShotType:
        return SHOT_TYPES[int(self.probs.argmax())]

    @property
    def conf(self) -> float:
        return float(self.probs.max())


class ShotClassifier:
    """``ShotClassifier(GbmModel.load(p), detector).classify([bgr, ...]) -> [ShotProbs, ...]``."""

    def __init__(self, model: GbmModel, detector: DFineDetector | None = None) -> None:
        self.model = model
        self.detector = detector
        feats = tuple(model.features)
        if feats == COLOUR_FEATURES:
            self.with_persons = False
        elif feats == COLOUR_FEATURES + PERSON_FEATURES:
            self.with_persons = True
            if detector is None:
                raise ValueError(f"model {model.name!r} needs person features: pass a detector ({model.detector})")
        else:
            raise ValueError(f"model {model.name!r} features {feats} do not match this cv_core's feature set")
        # model class index → ShotType index
        self._to_shot = np.zeros((len(model.classes), len(SHOT_TYPES)))
        for i, c in enumerate(model.classes):
            st = ShotType(model.class_map.get(c, c))
            self._to_shot[i, SHOT_TYPES.index(st)] = 1.0

    def features(self, images: Sequence[np.ndarray]) -> np.ndarray:
        cols = np.stack([colour_features(im) for im in images]) if images else np.zeros((0, len(COLOUR_FEATURES)))
        if not self.with_persons:
            return cols
        assert self.detector is not None
        dets = self.detector(images)
        pers = np.stack([person_features(d, im.shape[0]) for d, im in zip(dets, images, strict=True)])
        return np.hstack([cols, pers])

    def predict_features(self, X: np.ndarray) -> list[ShotProbs]:
        if len(X) == 0:
            return []
        p = self.model.predict_proba(X) @ self._to_shot
        return [ShotProbs(row) for row in p]

    def classify(self, images: Sequence[np.ndarray]) -> list[ShotProbs]:
        return self.predict_features(self.features(images))
