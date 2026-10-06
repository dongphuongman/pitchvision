"""S5 stitch stage (M1-CV-13): detections.parquet per segment (+ optional homography.parquet) → tracks.parquet."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from cv_core.io.parquet import CLS_BALL, CLS_PLAYER, NO_TRACK, read_tracks, write_detections, write_homography
from pitchvision_schemas import PipelineConfig, Segment, Shot, ShotTimeline, ShotType
from pitchvision_worker.stages.stitch import SegmentInput, pitch_summary, run_stitch

FPS = 25


def detections(path: Path, seg: Segment, players: dict[int, float], *, ball: bool = True) -> Path:
    f0, f1 = seg.t0_ms * FPS // 1000, seg.t1_ms * FPS // 1000
    rows: dict[str, list[float]] = {k: [] for k in ("frame_idx", "ts_ms", "track_id", "cls", "team", "x1", "y1", "x2",
                                                   "y2", "conf")}
    for f in range(f0, f1):
        for local, x in players.items():
            for k, v in zip(("frame_idx", "ts_ms", "track_id", "cls", "team", "x1", "y1", "x2", "y2", "conf"),
                            (f, f * 40, local, CLS_PLAYER, local % 2, x, 300, x + 20, 350, 0.9), strict=True):
                rows[k].append(v)
        if ball:
            for k, v in zip(("frame_idx", "ts_ms", "track_id", "cls", "team", "x1", "y1", "x2", "y2", "conf"),
                            (f, f * 40, NO_TRACK, CLS_BALL, -1, 600, 400, 606, 406, 0.4), strict=True):
                rows[k].append(v)
    return write_detections(path, {k: np.asarray(v) for k, v in rows.items()}, {"segment": seg.model_dump()})


TIMELINE = ShotTimeline(shots=[Shot(t0_ms=0, t1_ms=8000, type=ShotType.main_wide, conf=0.9),
                               Shot(t0_ms=8000, t1_ms=10_000, type=ShotType.replay, conf=0.9),
                               Shot(t0_ms=10_000, t1_ms=14_000, type=ShotType.main_wide, conf=0.9)])  # fmt: skip


def test_run_stitch_links_inside_shot_only(tmp_path: Path) -> None:
    s0, s1, s2 = Segment(index=0, t0_ms=0, t1_ms=4000), Segment(index=1, t0_ms=4000, t1_ms=8000), \
        Segment(index=2, t0_ms=10_000, t1_ms=14_000)
    inputs = [SegmentInput(s, detections(tmp_path / f"d{s.index}.parquet", s, {1: 100.0, 2: 500.0}))
              for s in (s0, s1, s2)]
    out = run_stitch(inputs, TIMELINE, tmp_path / "out", PipelineConfig())
    t = read_tracks(out.paths["tracks"])
    ids = t.column("track_id").to_numpy()
    assert out.links == 2 and out.tracks == 4  # 0→1 linked (keyframe split); 2 is after a replay → new ids
    assert sorted(set(ids[t.column("frame_idx").to_numpy() < 200].tolist())) == [-1, 1, 2]
    assert sorted(set(ids[t.column("frame_idx").to_numpy() >= 250].tolist())) == [-1, 3, 4]
    assert out.rows == t.num_rows == 3 * 100 * 3
    assert out.pitch.status == "unavailable" and out.pitch.homography_ok_pct == 0.0
    assert out.pitch.main_wide_frames == 300
    assert np.isnan(t.column("px").to_numpy()).all()


def test_run_stitch_projects_with_homography(tmp_path: Path) -> None:
    s0 = Segment(index=0, t0_ms=0, t1_ms=4000)
    det = detections(tmp_path / "d0.parquet", s0, {1: 290.0}, ball=False)
    H = np.array([[0.1, 0, -10], [0, 0.1, -10], [0, 0, 1]], float)
    frames = np.arange(0, 100)
    Hs = np.repeat(H[None], 100, axis=0)
    q = np.where(frames < 60, 0.9, 0.2).astype(np.float32)  # 40 % of the segment below 0.5
    hom = write_homography(tmp_path / "h0.parquet", frames, Hs, q)
    tl = ShotTimeline(shots=[Shot(t0_ms=0, t1_ms=4000, type=ShotType.main_wide, conf=0.9)])
    out = run_stitch([SegmentInput(s0, det, hom)], tl, tmp_path / "out")
    t = read_tracks(out.paths["tracks"])
    assert t.column("px").to_numpy()[0] == pytest.approx(20.0) and t.column("py").to_numpy()[0] == pytest.approx(25.0)
    assert out.pitch.status == "degraded" and out.pitch.homography_ok_pct == 60.0 and out.pitch.segments_with_h == 1


def test_pitch_summary_counts_frames_without_h_as_bad() -> None:
    from cv_core.track import FrameHomographies

    tl = ShotTimeline(shots=[Shot(t0_ms=0, t1_ms=4000, type=ShotType.main_wide, conf=0.9),
                             Shot(t0_ms=4000, t1_ms=8000, type=ShotType.close_up, conf=0.9)])  # fmt: skip
    hom = FrameHomographies(np.arange(0, 80), np.repeat(np.eye(3)[None], 80, axis=0), np.full(80, 0.9, np.float32))
    s = pitch_summary(tl, hom, 25.0, 1)
    assert (s.status, s.homography_ok_pct, s.main_wide_frames) == ("ok", 80.0, 100)
    hom75 = FrameHomographies(np.arange(0, 60), np.repeat(np.eye(3)[None], 60, axis=0), np.full(60, 0.9, np.float32))
    assert pitch_summary(tl, hom75, 25.0, 1).status == "degraded"
    assert pitch_summary(tl, None, 25.0, 0).status == "unavailable"
