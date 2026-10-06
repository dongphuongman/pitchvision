"""ByteTrack (Zhang et al., ECCV 2022) — compact re-implementation for S4 (AD §6.3).

Follows the reference algorithm (github.com/ifzhang/ByteTrack, MIT): Kalman-predicted tracks are matched to
high-score detections by IoU (fused with the score), the leftovers to low-score detections, unconfirmed tracks to
the remaining high ones; new tracks start from unmatched high detections ≥ ``new_track_thresh``; lost tracks are
kept ``track_buffer`` frames (scaled to the frame rate) for re-identification by IoU only. No Ultralytics code.

PitchVision additions:
- ``can_init`` per detection: only on-pitch detections may start a track; off-pitch ones can still continue one
  (a winger on the touchline where the grass mask is imperfect) — S4 records them with ``on_pitch = false``;
- defaults tuned on SP-05's two 90 s broadcast windows (cached D-FINE detections, grid over buffer / match /
  new-track thresholds): IDs per visible player 17.8 / 18.6 with the reference values → 14.5 / 10.3 (SP-05's
  Ultralytics ByteTrack + YOLO11m: 22.9 / 23.9). No ID ground truth yet, so the ID-switch cost of the looser
  match threshold is unmeasured (G0 tracking labels);
- every returned track carries ``det_index``: the detection it was matched to in this frame, so S4 writes the
  detector's box / score (not the Kalman estimate).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

import numpy as np

from cv_core.track import kalman
from cv_core.track.assignment import linear_assignment


class TrackState(IntEnum):
    new = 0
    tracked = 1
    lost = 2
    removed = 3


@dataclass(frozen=True)
class ByteTrackConfig:
    track_high_thresh: float = 0.35   # = PipelineConfig.conf_threshold: first association + "real" detections
    track_low_thresh: float = 0.1     # second association floor (the detector must be run at ≤ this)
    new_track_thresh: float = 0.6     # a track starts only from a detection this confident
    match_thresh: float = 0.9         # IoU distance limit, first association (reference 0.8)
    low_match_thresh: float = 0.5     # second association
    unconfirmed_match_thresh: float = 0.7
    track_buffer: int = 60            # frames at 30 fps a lost track is kept (scaled by frame_rate / 30; ref. 30)
    fuse_score: bool = True
    duplicate_iou_dist: float = 0.15


@dataclass
class Track:
    track_id: int
    mean: np.ndarray
    cov: np.ndarray
    score: float
    start_frame: int
    frame_id: int                    # last frame it was matched
    state: TrackState = TrackState.new
    is_activated: bool = False
    tracklet_len: int = 0
    det_index: int = -1              # detection matched in the current frame, -1 if none
    hits: int = 1
    extra: dict[str, object] = field(default_factory=dict)

    @property
    def xyxy(self) -> np.ndarray:
        return kalman.xyah_to_xyxy(self.mean[:4])


def iou_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)))
    x1 = np.maximum(a[:, None, 0], b[None, :, 0])
    y1 = np.maximum(a[:, None, 1], b[None, :, 1])
    x2 = np.minimum(a[:, None, 2], b[None, :, 2])
    y2 = np.minimum(a[:, None, 3], b[None, :, 3])
    inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
    area_a = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    area_b = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    return np.asarray(inter / np.maximum(area_a[:, None] + area_b[None, :] - inter, 1e-9))


class ByteTracker:
    """``tracker.update(boxes_xyxy, scores, can_init) -> list[Track]`` (activated tracks matched this frame)."""

    def __init__(self, frame_rate: float = 25.0, config: ByteTrackConfig | None = None) -> None:
        self.cfg = config or ByteTrackConfig()
        self.frame_id = 0
        self.max_time_lost = max(1, int(frame_rate / 30.0 * self.cfg.track_buffer))
        self.tracked: list[Track] = []
        self.lost: list[Track] = []
        self._next_id = 1
        self.created = 0

    def _new_id(self) -> int:
        i = self._next_id
        self._next_id += 1
        self.created += 1
        return i

    def _distance(self, tracks: list[Track], boxes: np.ndarray, scores: np.ndarray) -> np.ndarray:
        if not tracks or len(boxes) == 0:
            return np.zeros((len(tracks), len(boxes)))
        iou = iou_matrix(np.array([t.xyxy for t in tracks]), boxes)
        if self.cfg.fuse_score:
            iou = iou * scores[None, :]
        return 1.0 - iou

    def _update_track(self, t: Track, box: np.ndarray, score: float, det: int, reactivate: bool) -> None:
        t.mean, t.cov = kalman.update(t.mean, t.cov, kalman.xyxy_to_xyah(box))
        t.tracklet_len = 0 if reactivate else t.tracklet_len + 1
        t.state, t.is_activated = TrackState.tracked, True
        t.frame_id, t.score, t.det_index = self.frame_id, score, det
        t.hits += 1

    def update(self, boxes: np.ndarray, scores: np.ndarray, can_init: np.ndarray | None = None) -> list[Track]:
        self.frame_id += 1
        cfg = self.cfg
        boxes = np.asarray(boxes, np.float64).reshape(-1, 4)
        scores = np.asarray(scores, np.float64).reshape(-1)
        can = np.ones(len(scores), bool) if can_init is None else np.asarray(can_init, bool)
        for t in self.tracked + self.lost:
            t.det_index = -1

        high = np.flatnonzero(scores >= cfg.track_high_thresh)
        low = np.flatnonzero((scores >= cfg.track_low_thresh) & (scores < cfg.track_high_thresh))

        unconfirmed = [t for t in self.tracked if not t.is_activated]
        confirmed = [t for t in self.tracked if t.is_activated]
        pool = confirmed + [t for t in self.lost if t not in confirmed]
        if pool:
            means = np.array([t.mean for t in pool])
            for k, t in enumerate(pool):
                if t.state != TrackState.tracked:
                    means[k, 7] = 0.0
            means, covs = kalman.predict(means, np.array([t.cov for t in pool]))
            for k, t in enumerate(pool):
                t.mean, t.cov = means[k], covs[k]
        if unconfirmed:
            means, covs = kalman.predict(np.array([t.mean for t in unconfirmed]),
                                         np.array([t.cov for t in unconfirmed]))
            for k, t in enumerate(unconfirmed):
                t.mean, t.cov = means[k], covs[k]

        activated: list[Track] = []
        refound: list[Track] = []
        lost_now: list[Track] = []
        removed: list[Track] = []

        # 1st association: all pool tracks ↔ high detections
        d = self._distance(pool, boxes[high], scores[high])
        m, u_track, u_det = linear_assignment(d, cfg.match_thresh)
        for ti, di in m:
            t, det = pool[ti], int(high[di])
            was_tracked = t.state == TrackState.tracked
            self._update_track(t, boxes[det], float(scores[det]), det, reactivate=not was_tracked)
            (activated if was_tracked else refound).append(t)
        rest_high = high[u_det]

        # 2nd association: still-tracked leftovers ↔ low detections (IoU only)
        r_tracked = [pool[i] for i in u_track if pool[i].state == TrackState.tracked]
        d = 1.0 - iou_matrix(np.array([t.xyxy for t in r_tracked]).reshape(-1, 4), boxes[low])
        m, u_track2, _ = linear_assignment(d, cfg.low_match_thresh)
        for ti, di in m:
            t, det = r_tracked[ti], int(low[di])
            self._update_track(t, boxes[det], float(scores[det]), det, reactivate=False)
            activated.append(t)
        for i in u_track2:
            t = r_tracked[i]
            if t.state != TrackState.lost:
                t.state = TrackState.lost
                lost_now.append(t)

        # unconfirmed (one-frame) tracks ↔ remaining high detections
        d = self._distance(unconfirmed, boxes[rest_high], scores[rest_high])
        m, u_unc, u_det = linear_assignment(d, cfg.unconfirmed_match_thresh)
        for ti, di in m:
            t, det = unconfirmed[ti], int(rest_high[di])
            self._update_track(t, boxes[det], float(scores[det]), det, reactivate=False)
            activated.append(t)
        for i in u_unc:
            unconfirmed[i].state = TrackState.removed
            removed.append(unconfirmed[i])
        rest_high = rest_high[u_det]

        # new tracks
        for det in rest_high:
            if scores[det] < cfg.new_track_thresh or not can[det]:
                continue
            mean, cov = kalman.initiate(kalman.xyxy_to_xyah(boxes[det]))
            t = Track(track_id=self._new_id(), mean=mean, cov=cov, score=float(scores[det]),
                      start_frame=self.frame_id, frame_id=self.frame_id, state=TrackState.tracked,
                      is_activated=self.frame_id == 1, det_index=int(det))
            activated.append(t)

        for t in self.lost:
            if t.state == TrackState.lost and self.frame_id - t.frame_id > self.max_time_lost:
                t.state = TrackState.removed
                removed.append(t)

        alive = {id(t): t for t in self.tracked if t.state == TrackState.tracked}
        for t in activated + refound:
            alive[id(t)] = t
        tracked = list(alive.values())
        lost = [t for t in self.lost + lost_now if t.state == TrackState.lost]
        lost = [t for t in lost if id(t) not in alive]
        self.tracked, self.lost = self._dedupe(tracked, lost)
        return [t for t in self.tracked if t.is_activated and t.frame_id == self.frame_id]

    def _dedupe(self, tracked: list[Track], lost: list[Track]) -> tuple[list[Track], list[Track]]:
        """Drop the younger of a tracked/lost pair that overlaps almost completely (reference remove_duplicate)."""
        if not tracked or not lost:
            return tracked, lost
        d = 1.0 - iou_matrix(np.array([t.xyxy for t in tracked]), np.array([t.xyxy for t in lost]))
        drop_t, drop_l = set(), set()
        for i, j in zip(*np.nonzero(d < self.cfg.duplicate_iou_dist), strict=True):
            age_t = tracked[i].frame_id - tracked[i].start_frame
            age_l = lost[j].frame_id - lost[j].start_frame
            if age_t > age_l:
                drop_l.add(int(j))
            else:
                drop_t.add(int(i))
        return ([t for i, t in enumerate(tracked) if i not in drop_t],
                [t for j, t in enumerate(lost) if j not in drop_l])
