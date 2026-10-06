"""Segment-plan stage wrapper: PipelineConfig → cv_core.segments (rules are property-tested in cv-core)."""
from __future__ import annotations

from pitchvision_schemas import PipelineConfig, Shot, ShotTimeline, ShotType
from pitchvision_worker.stages.segments import plan_segments


def _tl(*spec: tuple[int, ShotType]) -> ShotTimeline:
    shots, t = [], 0
    for ms, kind in spec:
        shots.append(Shot(t0_ms=t, t1_ms=t + ms, type=kind, conf=0.9))
        t += ms
    return ShotTimeline(shots=shots)


def test_plan_uses_config() -> None:
    tl = _tl((100_000, ShotType.main_wide), (100_000, ShotType.main_wide), (20_000, ShotType.replay),
             (500, ShotType.main_wide))
    segs = plan_segments(tl, 220_500, PipelineConfig(segment_target_s=60))
    assert [s.index for s in segs] == [0, 1, 2, 3]
    assert all(s.t1_ms - s.t0_ms <= 60_000 for s in segs)
    assert segs[0].t0_ms == 0 and segs[-1].t1_ms == 200_000  # the 0.5 s wide run is dropped (min_shot_ms)
    assert plan_segments(tl, 220_500, PipelineConfig())[0].t1_ms == 100_000  # default target 120 s: no split


def test_plan_defaults_and_empty() -> None:
    assert plan_segments(_tl((5_000, ShotType.close_up)), 5_000) == []
