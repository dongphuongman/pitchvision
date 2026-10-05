"""Whole-video shot segmentation: one sequential decode → cuts (every frame) + classification (every second)."""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field

import numpy as np
from pitchvision_schemas import ShotTimeline

from cv_core.io.video import VideoReader
from cv_core.shots.classifier import ShotClassifier, ShotProbs
from cv_core.shots.cuts import CutDetector
from cv_core.shots.features import ANALYSIS_LONG_SIDE
from cv_core.shots.timeline import build_timeline

log = logging.getLogger(__name__)


@dataclass
class SegmentResult:
    timeline: ShotTimeline
    samples: list[tuple[int, ShotProbs]]
    cuts_ms: list[int]
    frames: int
    fps: float
    timings_s: dict[str, float] = field(default_factory=dict)


def segment_video(src: str | os.PathLike[str], classifier: ShotClassifier, *, fps: float = 25.0,
                  sample_every_s: float = 1.0, min_shot_ms: int = 1000, batch: int = 8,
                  cut_detector: CutDetector | None = None, t0_s: float | None = None,
                  t1_s: float | None = None) -> SegmentResult:
    """Classify ``src`` once per ``sample_every_s`` (frames ``idx % round(fps × s) == 0``) and detect cuts on every
    frame, on the ``fps`` timeline (the normalised video is already 25 fps, so no resampling happens)."""
    every = max(1, round(fps * sample_every_s))
    reader = VideoReader(src, t0_s, t1_s, target_fps=fps, long_side=ANALYSIS_LONG_SIDE)
    cuts = cut_detector or CutDetector()
    ms_per_frame = 1000.0 / reader.fps
    samples: list[tuple[int, ShotProbs]] = []
    cut_ms: list[int] = []
    pending: list[tuple[int, np.ndarray]] = []
    t_cls = t_cut = 0.0
    n = last_idx = 0
    first_idx: int | None = None

    def flush() -> None:
        nonlocal t_cls
        t = time.perf_counter()
        probs = classifier.classify([im for _, im in pending])
        samples.extend((round(i * ms_per_frame), p) for (i, _), p in zip(pending, probs, strict=True))
        pending.clear()
        t_cls += time.perf_counter() - t

    t_start = time.perf_counter()
    for fr in reader:
        if first_idx is None:
            first_idx = fr.idx
        n += 1
        last_idx = fr.idx
        t = time.perf_counter()
        if cuts.update(fr.idx, fr.image):
            cut_ms.append(round(fr.idx * ms_per_frame))
        t_cut += time.perf_counter() - t
        if fr.idx % every == 0:
            pending.append((fr.idx, fr.image))
            if len(pending) >= batch:
                flush()
    if pending:
        flush()
    total = time.perf_counter() - t_start
    start_ms = round((first_idx or 0) * ms_per_frame)
    end_ms = round((last_idx + 1) * ms_per_frame) if n else start_ms
    # timeline relative to the video start (t0_s offsets are kept: shots are on the video timeline)
    tl = build_timeline([(t - start_ms, p) for t, p in samples], [c - start_ms for c in cut_ms], end_ms - start_ms,
                        min_shot_ms=min_shot_ms)
    if start_ms:
        tl = ShotTimeline(shots=[s.model_copy(update={"t0_ms": s.t0_ms + start_ms, "t1_ms": s.t1_ms + start_ms})
                                 for s in tl.shots])
    timings = {"total": total, "classify": t_cls, "cuts": t_cut, "decode": total - t_cls - t_cut}
    log.info("S2b: %d frames, %d samples, %d cuts, %d shots in %.1fs (decode %.1f, classify %.1f, cuts %.1f)",
             n, len(samples), len(cut_ms), len(tl.shots), total, timings["decode"], t_cls, t_cut)
    return SegmentResult(timeline=tl, samples=samples, cuts_ms=cut_ms, frames=n, fps=reader.fps, timings_s=timings)
