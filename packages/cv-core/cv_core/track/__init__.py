"""Multi-object tracking (S4, AD §6.3): ByteTrack on per-frame detections, segment-local IDs."""
from cv_core.track.assignment import hungarian, linear_assignment
from cv_core.track.bytetrack import ByteTrackConfig, ByteTracker, Track, TrackState, iou_matrix

__all__ = ["ByteTrackConfig", "ByteTracker", "Track", "TrackState", "hungarian", "iou_matrix", "linear_assignment"]
