"""Shot segmentation (S2b, M1-CV-06): per-frame shot type + cuts → ``ShotTimeline``."""
from cv_core.shots.classifier import SHOT_TYPES, ShotClassifier, ShotProbs
from cv_core.shots.cuts import CutDetector, frame_histogram, hist_distance
from cv_core.shots.features import COLOUR_FEATURES, PERSON_FEATURES, colour_features, feature_names, person_features
from cv_core.shots.gbm import GbmModel, Tree
from cv_core.shots.segment import SegmentResult, segment_video
from cv_core.shots.timeline import build_timeline

__all__ = [
    "COLOUR_FEATURES",
    "PERSON_FEATURES",
    "SHOT_TYPES",
    "CutDetector",
    "GbmModel",
    "SegmentResult",
    "ShotClassifier",
    "ShotProbs",
    "Tree",
    "build_timeline",
    "colour_features",
    "feature_names",
    "frame_histogram",
    "hist_distance",
    "person_features",
    "segment_video",
]
