"""S2b shot segmentation (AD §6.2 v1.2, M1-CV-06 basic version; full S2b incl. DB write + segment plan = M1-CV-08).

``normalized.mp4`` → ``ShotTimeline`` (cuts on every frame, shot type once per second, merge < ``min_shot_ms``).
Thin wrapper over ``cv_core.shots``; the task layer persists the timeline (shots table + manifest).
Models come from ``models/registry.yaml`` (weights in ``PITCHVISION_MODEL_DIR``, sha256-checked).
"""
from __future__ import annotations

import logging
from pathlib import Path

from cv_core.detect import DFineDetector
from cv_core.registry import ModelRegistry
from cv_core.runtime import Runtime
from cv_core.shots import GbmModel, SegmentResult, ShotClassifier, segment_video
from pitchvision_schemas import PipelineConfig, ShotTimeline

log = logging.getLogger(__name__)

SHOTS_MODEL = "shots-gbm-det"
SHOTS_MODEL_FALLBACK = "shots-gbm-colour"  # detector-free (lower accuracy; see models/shots-gbm.md)


def load_classifier(config: PipelineConfig | None = None, *, registry: ModelRegistry | None = None,
                    runtime: Runtime | None = None, model: str = SHOTS_MODEL) -> ShotClassifier:
    cfg = config or PipelineConfig()
    reg = registry or ModelRegistry.load()
    gbm = GbmModel.load(reg.resolve(model))
    detector = None
    if gbm.detector:
        rt = runtime or Runtime.select()
        detector = DFineDetector(rt.model(str(reg.resolve(gbm.detector))), imgsz=cfg.detector_imgsz,
                                 conf=cfg.conf_threshold)
    return ShotClassifier(gbm, detector)


def run_shots(normalized: Path, config: PipelineConfig | None = None, *,
              classifier: ShotClassifier | None = None) -> ShotTimeline:
    """Shot timeline of ``normalized`` (the S2 output, 25 fps) — contiguous, covers [0, duration)."""
    return run_shots_detailed(normalized, config, classifier=classifier).timeline


def run_shots_detailed(normalized: Path, config: PipelineConfig | None = None, *,
                       classifier: ShotClassifier | None = None) -> SegmentResult:
    cfg = config or PipelineConfig()
    clf = classifier or load_classifier(cfg)
    res = segment_video(normalized, clf, fps=cfg.target_fps, min_shot_ms=cfg.min_shot_ms)
    log.info("S2b %s: %d shots, main_wide coverage %.1f%%", normalized.name, len(res.timeline.shots),
             100 * res.timeline.coverage())
    return res
