"""Segment planning (S2b → S4 fan-out, M1-CV-08): shot timeline → half-open S4 work units on the 25 fps timeline.

Rules (contract ``docs/contracts/pipeline-stages.md``, AD §6.1):

1. Only shots whose type is in ``analyze_types`` (default ``main_wide``) are analysed.
2. Adjacent analysable shots (``a.t1 == b.t0``, e.g. a wide→wide cut or a false cut inside a pan) form a *run*:
   a segment never spans non-analysable time, because S4 only gets ``[t0, t1)`` and runs on every frame in it.
3. Runs shorter than ``min_shot_ms`` are dropped (too short to track anything; S2b already merges < 1 s shots).
4. Inside a run, consecutive shots are packed greedily into segments of at most ``target_ms``. A shot is never
   split unless it alone exceeds ``target_ms``; then it is cut into near-equal chunks at multiples of
   ``split_ms`` (the 2 s GOP of ``normalized.mp4``, so every S4 reader starts on a keyframe), each ≤ ``target_ms``.
5. Boundaries are snapped to the frame grid (``1000 / fps`` ms) and clipped to ``[0, duration_ms)``;
   indices are contiguous from 0, segments are sorted and never overlap.

No overlap between neighbouring segments in v1: S5 v1 (M1-CV-13) joins tracks on the shared boundary frame.
"""
from __future__ import annotations

import itertools
import math
from collections.abc import Iterable, Sequence

from pitchvision_schemas import Segment, Shot, ShotTimeline


def _frame_ms(fps: float) -> float:
    return 1000.0 / fps


def _snap(ms: float, fps: float) -> int:
    """Round to the nearest frame boundary (ms of frame k = round(k × 1000 / fps))."""
    k = math.floor(ms / _frame_ms(fps) + 0.5)
    return round(k * _frame_ms(fps))


def split_points(t0: int, t1: int, target_ms: int, split_ms: int) -> list[int]:
    """Interior cut points of ``[t0, t1)`` at multiples of ``split_ms`` so that every chunk is ≤ ``target_ms``.

    Uses the fewest near-equal chunks that satisfy the bound (ideal cuts ``t0 + k·L/n`` rounded to the nearest
    multiple of ``split_ms``). If ``target_ms < split_ms`` the bound is unreachable: cut at every multiple.
    """
    length = t1 - t0
    if length <= target_ms:
        return []
    every = [b for b in range(math.ceil((t0 + 1) / split_ms) * split_ms, t1, split_ms)]
    if target_ms < split_ms or not every:
        return every
    n = math.ceil(length / target_ms)
    while length / n >= split_ms:
        cuts = sorted({round((t0 + k * length / n) / split_ms) * split_ms for k in range(1, n)})
        cuts = [c for c in cuts if t0 < c < t1]
        edges = [t0, *cuts, t1]
        if len(cuts) == n - 1 and all(0 < b - a <= target_ms for a, b in itertools.pairwise(edges)):
            return cuts
        n += 1
    return every


def _runs(shots: Iterable[Shot], analyze: frozenset[str]) -> list[list[tuple[int, int]]]:
    runs: list[list[tuple[int, int]]] = []
    prev_end: int | None = None
    for s in shots:
        if str(s.type) not in analyze:
            prev_end = None
            continue
        if prev_end is not None and s.t0_ms == prev_end and runs:
            runs[-1].append((s.t0_ms, s.t1_ms))
        else:
            runs.append([(s.t0_ms, s.t1_ms)])
        prev_end = s.t1_ms
    return runs


def plan_segments(timeline: ShotTimeline | Sequence[Shot], duration_ms: int, *,
                  analyze_types: Iterable[str] = ("main_wide",), min_shot_ms: int = 1000,
                  target_ms: int = 120_000, split_ms: int = 2000, fps: float = 25.0) -> list[Segment]:
    """S4 work units for ``timeline`` (see module docstring for the rules)."""
    if target_ms <= 0 or split_ms <= 0:
        raise ValueError("target_ms and split_ms must be > 0")
    shots = timeline.shots if isinstance(timeline, ShotTimeline) else list(timeline)
    end = _snap(duration_ms, fps) if duration_ms > 0 else 0
    clipped = []
    for s in shots:
        t0, t1 = _snap(max(0, s.t0_ms), fps), min(_snap(s.t1_ms, fps), end)
        if t1 > t0:
            clipped.append(s.model_copy(update={"t0_ms": t0, "t1_ms": t1}))
    spans: list[tuple[int, int]] = []
    for run in _runs(clipped, frozenset(str(t) for t in analyze_types)):
        if run[-1][1] - run[0][0] < min_shot_ms:
            continue
        cur: tuple[int, int] | None = None  # open segment
        for t0, t1 in run:
            if cur is not None and t1 - cur[0] <= target_ms:
                cur = (cur[0], t1)
                continue
            if cur is not None:
                spans.append(cur)
            if t1 - t0 <= target_ms:
                cur = (t0, t1)
                continue
            edges = [t0, *(_snap(c, fps) for c in split_points(t0, t1, target_ms, split_ms)), t1]
            spans += list(itertools.pairwise(edges[:-1]))
            cur = (edges[-2], t1)  # the last chunk may still absorb the following shots
        if cur is not None:
            spans.append(cur)
    return [Segment(index=i, t0_ms=a, t1_ms=b) for i, (a, b) in enumerate(spans) if b > a]


def segment_frames(segment: Segment, fps: float = 25.0) -> tuple[int, int]:
    """Half-open frame-index range ``[i0, i1)`` of a segment on the ``fps`` timeline (same rounding as VideoReader)."""
    return math.floor(segment.t0_ms * fps / 1000 + 0.5), math.floor(segment.t1_ms * fps / 1000 + 0.5)
