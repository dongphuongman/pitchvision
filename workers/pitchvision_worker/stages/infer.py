"""S4 infer v1 (AD §6.3, M1-CV-10): one segment → ``out_dir/detections.parquet`` (contract: pipeline-stages.md).

``VideoReader`` decodes ``[t0, t1)`` sequentially (one seek to the keyframe, never per frame — SP-05) with
``config.frame_stride`` → D-FINE (run at ``LOW_CONF`` so ByteTrack's second association gets the weak boxes) →
grass mask (only feet-on-grass persons may *start* a track) → ByteTrack (segment-local ids from 1) → torso crops
from each track's first observations → SigLIP → team model → per-track plurality vote. The ball is the best
``sports ball`` per frame (no tracking, ``track_id = -1``). ``px, py`` stay NaN until the W4 homography.

The ``_SUCCESS`` marker is written by the task layer after the upload, never here.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from cv_core.detect import COCO_PERSON, COCO_SPORTS_BALL, Detections
from cv_core.io.parquet import CLS_BALL, CLS_PLAYER, DETECTIONS_VERSION, NO_TRACK, write_detections
from cv_core.io.video import VideoReader
from cv_core.pitch import grass_mask
from cv_core.teams import Embedder, TeamModel, TrackTeamVoter, team_column
from cv_core.teams.calibrate import Detector
from cv_core.track import ByteTrackConfig, ByteTracker
from pitchvision_schemas import PipelineConfig, Segment

from pitchvision_worker.stages.models import detector_model_name, load_detector, load_embedder

log = logging.getLogger(__name__)

DETECTIONS_NAME = "detections.parquet"
LOW_CONF = 0.1          # detector floor = ByteTrack track_low_thresh
VOTES_PER_TRACK = 10    # AD §6.3
VOTE_EVERY = 5          # frames between two vote crops of one track
BATCH = 8


@dataclass(frozen=True)
class InferOutput:
    paths: dict[str, Path]                 # {"detections": out_dir/detections.parquet}
    frames: int                            # frames processed (after stride)
    fps_measured: float                    # frames / wall time of the whole stage
    stats: dict[str, Any] = field(default_factory=dict)


class _Rows:
    def __init__(self) -> None:
        self.cols: dict[str, list[Any]] = {k: [] for k in ("frame_idx", "ts_ms", "track_id", "cls", "x1", "y1",
                                                           "x2", "y2", "conf", "on_pitch")}

    def add(self, frame_idx: int, ts_ms: int, track_id: int, cls: int, box: np.ndarray, conf: float,
            on_pitch: bool) -> None:
        c = self.cols
        c["frame_idx"].append(frame_idx)
        c["ts_ms"].append(ts_ms)
        c["track_id"].append(track_id)
        c["cls"].append(cls)
        for k, v in zip(("x1", "y1", "x2", "y2"), box, strict=True):
            c[k].append(float(v))
        c["conf"].append(conf)
        c["on_pitch"].append(on_pitch)

    def arrays(self) -> dict[str, np.ndarray]:
        return {k: np.asarray(v) for k, v in self.cols.items()}


def _best_ball(balls: Detections, on: np.ndarray, conf: float) -> int | None:
    """Index of the frame's ball: the most confident on-pitch one, else the most confident (flagged off-pitch)."""
    ok = balls.scores >= conf
    if not ok.any():
        return None
    for pool in (ok & on, ok):
        if pool.any():
            idx = np.flatnonzero(pool)
            return int(idx[np.argmax(balls.scores[idx])])
    return None


def run_infer(normalized: Path, segment: Segment, team_model: Path | TeamModel, out_dir: Path,
              config: PipelineConfig | None = None, *, detector: Detector | None = None,
              embedder: Embedder | None = None, embedder_sha256: str | None = None,
              votes_per_track: int = VOTES_PER_TRACK, vote_every: int = VOTE_EVERY) -> InferOutput:
    cfg = config or PipelineConfig()
    t_all = time.perf_counter()
    model = team_model if isinstance(team_model, TeamModel) else TeamModel.load(team_model)
    detector_name = "injected"
    if detector is None:
        detector = load_detector(cfg, conf=LOW_CONF)
        detector_name = detector_model_name(cfg)
    if embedder is None:
        embedder, embedder_sha256 = load_embedder(name=model.embedder)
    if model.embedder_sha256 and embedder_sha256 and model.embedder_sha256 != embedder_sha256:
        raise RuntimeError(f"team model was fitted with {model.embedder}@{model.embedder_sha256[:12]}, "
                           f"S4 has {embedder_sha256[:12]}: embeddings are not comparable")

    stride = cfg.frame_stride
    reader = VideoReader(normalized, segment.t0_ms / 1000, segment.t1_ms / 1000, stride=stride,
                         target_fps=cfg.target_fps)
    fps = reader.fps
    tracker = ByteTracker(frame_rate=fps / stride, config=ByteTrackConfig(
        track_high_thresh=cfg.conf_threshold, track_low_thresh=LOW_CONF,
        new_track_thresh=max(ByteTrackConfig.new_track_thresh, cfg.conf_threshold + 0.1)))
    voter = TrackTeamVoter(votes=votes_per_track, every=vote_every * stride)
    rows = _Rows()
    n_frames = ball_frames = 0
    players_per_frame: list[int] = []
    t_detect = t_track = 0.0
    t_decode_start = time.perf_counter()
    for batch in reader.batches(BATCH):
        t = time.perf_counter()
        dets = detector([f.image for f in batch])
        t_detect += time.perf_counter() - t
        t = time.perf_counter()
        for fr, d in zip(batch, dets, strict=True):
            n_frames += 1
            ts = round(fr.idx * 1000 / fps)
            mask = grass_mask(fr.image)
            persons = d.of_class(COCO_PERSON)
            on = mask.on_pitch(persons.boxes)
            tracks = tracker.update(persons.boxes, persons.scores, can_init=on)
            players_per_frame.append(len(tracks))
            for tr in tracks:
                box = persons.boxes[tr.det_index]
                rows.add(fr.idx, ts, tr.track_id, CLS_PLAYER, box, float(persons.scores[tr.det_index]),
                         bool(on[tr.det_index]))
                voter.offer(tr.track_id, fr.idx, fr.image, box)
            balls = d.of_class(COCO_SPORTS_BALL)
            b_on = mask.on_pitch(balls.boxes)
            bi = _best_ball(balls, b_on, cfg.conf_threshold)
            if bi is not None:
                ball_frames += 1
                rows.add(fr.idx, ts, NO_TRACK, CLS_BALL, balls.boxes[bi], float(balls.scores[bi]), bool(b_on[bi]))
        t_track += time.perf_counter() - t
    t_loop = time.perf_counter() - t_decode_start

    t = time.perf_counter()
    teams, per_track = voter.resolve(model, embedder)
    t_team = time.perf_counter() - t
    cols = rows.arrays()
    cols["team"] = team_column(cols["track_id"], teams) if len(cols["track_id"]) else np.zeros(0, np.int8)
    is_player = cols["cls"] == CLS_PLAYER if len(cols["cls"]) else np.zeros(0, bool)
    track_ids = np.unique(cols["track_id"][is_player]) if is_player.any() else np.zeros(0, np.int64)
    team_of = [teams.get(int(t), -1) for t in track_ids]
    lengths: Any = np.bincount(np.searchsorted(track_ids, cols["track_id"][is_player])) if len(track_ids) else []
    stats: dict[str, Any] = {
        "segment": segment.model_dump(), "fps": fps, "stride": stride, "frames": n_frames, "rows": len(cols["ts_ms"]),
        "player_rows": int(is_player.sum()), "tracks": len(track_ids), "tracks_created": tracker.created,
        "tracks_per_min": round(len(track_ids) / max((segment.t1_ms - segment.t0_ms) / 60000, 1e-9), 1),
        "median_players_per_frame": float(np.median(players_per_frame)) if players_per_frame else 0.0,
        "median_track_frames": float(np.median(lengths)) if len(lengths) else 0.0,
        "ball_frames": ball_frames, "team_tracks": {str(k): team_of.count(k) for k in (0, 1, -1)},
        "vote_crops": len(voter.crops),
        "vote_unanimous": round(float(np.mean([len(set(v)) == 1 for v in per_track.values()])), 3)
        if per_track else None,
    }
    t = time.perf_counter()
    meta = {"stage": "S4", "version": DETECTIONS_VERSION, "segment": segment.model_dump(), "fps": fps,
            "stride": stride, "detector": detector_name, "detector_low_conf": LOW_CONF,
            "conf_threshold": cfg.conf_threshold, "embedder": model.embedder, "tracker": "bytetrack",
            "stats": stats}
    path = write_detections(Path(out_dir) / DETECTIONS_NAME, cols, meta)
    t_write = time.perf_counter() - t
    total = time.perf_counter() - t_all
    stats["timings_s"] = {k: round(v, 3) for k, v in {
        "decode_and_misc": t_loop - t_detect - t_track, "detect": t_detect, "track_mask_crops": t_track,
        "team_vote": t_team, "write": t_write, "total": total}.items()}
    fps_measured = n_frames / total if total > 0 else 0.0
    log.info("S4 seg %d [%d, %d) ms: %d frames, %d tracks, %d rows in %.1fs (%.1f fps)", segment.index,
             segment.t0_ms, segment.t1_ms, n_frames, len(track_ids), stats["rows"], total, fps_measured)
    return InferOutput(paths={"detections": path}, frames=n_frames, fps_measured=round(fps_measured, 2), stats=stats)
