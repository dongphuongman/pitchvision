"""Pitch calibration (S3/S4, AD §6): FIFA template, keypoint model, homography, line-alignment h_quality, smoothing."""
from cv_core.pitch.homography import (
    HomographyConfig,
    HomographyEstimate,
    HomographySmoother,
    estimate_homography,
    fit_homography,
    project,
)
from cv_core.pitch.keypoints import Letterbox, PitchKeypointModel, PitchKeypoints, decode, letterbox
from cv_core.pitch.lines import Alignment, LineMask, h_quality, line_alignment, line_mask, render_lines
from cv_core.pitch.template import (
    FLIP_INDEX,
    KEYPOINT_KINDS,
    KEYPOINT_NAMES,
    NUM_KEYPOINTS,
    FIFA_105x68,
    PitchLine,
    PitchTemplate,
)

__all__ = [
    "FLIP_INDEX",
    "KEYPOINT_KINDS",
    "KEYPOINT_NAMES",
    "NUM_KEYPOINTS",
    "Alignment",
    "FIFA_105x68",
    "HomographyConfig",
    "HomographyEstimate",
    "HomographySmoother",
    "Letterbox",
    "LineMask",
    "PitchKeypointModel",
    "PitchKeypoints",
    "PitchLine",
    "PitchTemplate",
    "decode",
    "estimate_homography",
    "fit_homography",
    "h_quality",
    "letterbox",
    "line_alignment",
    "line_mask",
    "project",
    "render_lines",
]
