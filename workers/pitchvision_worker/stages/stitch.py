"""S5 stitch v1 (AD §6.4/§6.5, M1-CV-13): every segment's ``detections.parquet`` → ``out_dir/tracks.parquet``.

Global track ids, links only across keyframe boundaries inside one shot (never across a cut in M1), ≤ 0.4 s gap
interpolation, ball rows kept — rules in ``cv_core.track.stitch``. When the optional pitch stage produced a
``homography.parquet`` for a segment, box bottom-centres are projected to pitch metres (``px, py``) and the frame's
``h_quality`` is copied to each row; otherwise both stay NaN.

The pitch calibration verdict of the job comes from here (contract: docs/contracts/pipeline-stages.md):
``unavailable`` without any homography, ``degraded`` when more than ``DEGRADED_SHARE`` of the main_wide frames have
``h_quality`` below ``H_QUALITY_OK`` (frames without H count as below), else ``ok``.
"""
from __future__ import annotations

import logging
import math
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np
from cv_core.io.parquet import TRACKS_VERSION, read_detections, read_homography, write_tracks
from cv_core.track import FrameHomographies, SegmentDetections, StitchConfig, stitch
from pitchvision_schemas import PipelineConfig, Segment, ShotTimeline, ShotType

log = logging.getLogger(__name__)

TRACKS_NAME = "tracks.parquet"
H_QUALITY_OK = 0.5      # manifest Coverage.homography_ok_pct threshold (M1-CV-17 re-tunes it on labelled frames)
DEGRADED_SHARE = 0.30   # AD: pitch "degraded" when > 30 % of main_wide frames are below H_QUALITY_OK

PitchStatus = Literal["ok", "degraded", "unavailable"]


@dataclass(frozen=True)
class SegmentInput:
    segment: Segment
    detections: Path
    homography: Path | None = None   # pitch stage output; None = no calibration for this segment


@dataclass(frozen=True)
class PitchSummary:
    status: PitchStatus
    homography_ok_pct: float          # % of main_wide frames with h_quality ≥ H_QUALITY_OK
    main_wide_frames: int
    segments_with_h: int


@dataclass(frozen=True)
class StitchOutput:
    paths: dict[str, Path]            # {"tracks": out_dir/tracks.parquet}
    rows: int
    tracks: int
    links: int
    pitch: PitchSummary
    stats: dict[str, Any] = field(default_factory=dict)


def _frame(ms: int, fps: float) -> int:
    return math.floor(ms * fps / 1000 + 0.5)


def pitch_summary(timeline: ShotTimeline, hom: FrameHomographies | None, fps: float, segments_with_h: int,
                  *, threshold: float = H_QUALITY_OK, degraded_share: float = DEGRADED_SHARE) -> PitchSummary:
    wide = [(_frame(s.t0_ms, fps), _frame(s.t1_ms, fps)) for s in timeline.shots if s.type == ShotType.main_wide]
    total = sum(max(0, b - a) for a, b in wide)
    if hom is None or not len(hom.frame_idx):
        return PitchSummary("unavailable", 0.0, total, 0)
    f0, _, q = hom.dense()
    ok = 0
    for a, b in wide:
        lo, hi = max(a, f0), min(b, f0 + len(q))
        if hi > lo:
            seg_q = q[lo - f0:hi - f0]
            ok += int(np.count_nonzero(np.nan_to_num(seg_q, nan=-1.0) >= threshold))
    pct = round(100.0 * ok / total, 2) if total else 0.0
    status: PitchStatus = "degraded" if total and (total - ok) / total > degraded_share else "ok"
    return PitchSummary(status, pct, total, segments_with_h)


def run_stitch(segments: Sequence[SegmentInput], timeline: ShotTimeline, out_dir: Path,
               config: PipelineConfig | None = None, *, stitch_config: StitchConfig | None = None) -> StitchOutput:
    cfg = config or PipelineConfig()
    t_all = time.perf_counter()
    scfg = stitch_config or StitchConfig(fps=float(cfg.target_fps))
    dets: list[SegmentDetections] = []
    homs: list[FrameHomographies] = []
    for si in segments:
        table = read_detections(si.detections)
        cols = {c: table.column(c).to_numpy(zero_copy_only=False) for c in table.column_names}
        dets.append(SegmentDetections(si.segment.index, si.segment.t0_ms, si.segment.t1_ms, cols))
        if si.homography is not None:
            homs.append(FrameHomographies(*read_homography(si.homography)))
    hom = FrameHomographies.concat(homs)
    cuts = [_frame(s.t0_ms, scfg.fps) for s in timeline.shots[1:]]
    res = stitch(dets, cut_frames=cuts, homography=hom, config=scfg)
    pitch = pitch_summary(timeline, hom, scfg.fps, len(homs))
    meta = {"stage": "S5", "version": TRACKS_VERSION, "fps": scfg.fps, "segments": [s.segment.model_dump()
            for s in segments], "stats": res.stats, "pitch": pitch.__dict__}  # fmt: skip
    path = write_tracks(Path(out_dir) / TRACKS_NAME, res.columns, meta)
    stats = {**res.stats, "elapsed_s": round(time.perf_counter() - t_all, 3)}
    log.info("S5: %d segment(s) → %d tracks, %d link(s), %d rows (%d interpolated), pitch %s %.1f%%",
             len(segments), res.stats["tracks"], len(res.links), res.stats["rows"], res.stats["interpolated_rows"],
             pitch.status, pitch.homography_ok_pct)
    return StitchOutput(paths={"tracks": path}, rows=res.stats["rows"], tracks=res.stats["tracks"],
                        links=len(res.links), pitch=pitch, stats=stats)
