"""Multi-object tracking (S4, AD §6.3): ByteTrack on per-frame detections, segment-local IDs; S5 stitch (AD §6.4)."""
from cv_core.track.assignment import hungarian, linear_assignment
from cv_core.track.bytetrack import ByteTrackConfig, ByteTracker, Track, TrackState, iou_matrix
from cv_core.track.stitch import (
    FrameHomographies,
    SegmentDetections,
    StitchConfig,
    StitchResult,
    project_rows,
    stitch,
)

__all__ = [
    "ByteTrackConfig",
    "ByteTracker",
    "FrameHomographies",
    "SegmentDetections",
    "StitchConfig",
    "StitchResult",
    "Track",
    "TrackState",
    "hungarian",
    "iou_matrix",
    "linear_assignment",
    "project_rows",
    "stitch",
]
