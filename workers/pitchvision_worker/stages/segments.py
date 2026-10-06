"""Segment plan (S2b → S4 fan-out, M1-CV-08): ``ShotTimeline`` → ``list[Segment]``, run in the orchestrator (CPU).

Only ``config.analyze_shot_types`` shots; adjacent analysable shots are merged up to ``config.segment_target_s``;
a shot is split (at ``config.keyframe_interval_s`` boundaries) only when it alone is longer than the target;
runs shorter than ``config.min_shot_ms`` are dropped. Rules + rationale: ``cv_core.segments``.

An empty plan means the video has no analysable footage: the orchestrator fails the job with ``NO_WIDE_SHOTS``.
The S2b output itself (``ShotTimeline``) is what the task layer writes to the ``shots`` table / ``shots.json``.
"""
from __future__ import annotations

from cv_core.segments import plan_segments as _plan
from pitchvision_schemas import PipelineConfig, Segment, ShotTimeline


def plan_segments(timeline: ShotTimeline, duration_ms: int, config: PipelineConfig | None = None) -> list[Segment]:
    cfg = config or PipelineConfig()
    return _plan(timeline, duration_ms, analyze_types=cfg.analyze_shot_types, min_shot_ms=cfg.min_shot_ms,
                 target_ms=cfg.segment_target_s * 1000, split_ms=cfg.keyframe_interval_s * 1000,
                 fps=cfg.target_fps)
