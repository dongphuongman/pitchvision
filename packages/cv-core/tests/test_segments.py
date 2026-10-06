"""Segment planning (M1-CV-08): examples + randomised property tests (hand-written; hypothesis is not a dep)."""
from __future__ import annotations

import itertools
import random

import pytest
from cv_core.segments import plan_segments, segment_frames, split_points
from pitchvision_schemas import Segment, Shot, ShotTimeline, ShotType

W, C, R, OT = ShotType.main_wide, ShotType.close_up, ShotType.replay, ShotType.other
FRAME = 40


def tl(*spec: tuple[int, ShotType]) -> ShotTimeline:
    shots, t = [], 0
    for ms, kind in spec:
        shots.append(Shot(t0_ms=t, t1_ms=t + ms, type=kind, conf=0.9))
        t += ms
    return ShotTimeline(shots=shots)


def spans(segs: list[Segment]) -> list[tuple[int, int]]:
    return [(s.t0_ms, s.t1_ms) for s in segs]


# ------------------------------------------------------------------ examples
def test_only_wide_and_merged_up_to_target() -> None:
    t = tl((30_000, W), (40_000, W), (5_000, C), (60_000, W), (70_000, W), (10_000, R))
    segs = plan_segments(t, 215_000, target_ms=120_000)
    # 30+40 merged; close-up breaks the run; 60+70 > 120 → two segments, no shot split
    assert spans(segs) == [(0, 70_000), (75_000, 135_000), (135_000, 205_000)]
    assert [s.index for s in segs] == [0, 1, 2]


def test_long_shot_split_on_keyframes_balanced() -> None:
    t = tl((1_000, C), (300_000, W))
    segs = plan_segments(t, 301_000, target_ms=120_000, split_ms=2000)
    assert len(segs) == 3
    for a, b in itertools.pairwise(segs):
        assert a.t1_ms == b.t0_ms and a.t1_ms % 2000 == 0
    assert all(s.t1_ms - s.t0_ms <= 120_000 for s in segs)
    assert min(s.t1_ms - s.t0_ms for s in segs) >= 90_000  # near-equal chunks, no tiny tail


def test_short_runs_dropped_and_duration_clip() -> None:
    t = tl((800, W), (5_000, C), (3_000, W), (2_000, OT), (10_000, W))
    assert spans(plan_segments(t, 15_000, min_shot_ms=1000)) == [(5_800, 8_800), (10_800, 15_000)]


def test_no_wide_shots() -> None:
    assert plan_segments(tl((10_000, C), (5_000, R)), 15_000) == []


def test_last_chunk_absorbs_following_shots() -> None:
    t = tl((130_000, W), (10_000, W))
    segs = plan_segments(t, 140_000, target_ms=120_000)
    assert spans(segs) == [(0, 64_000), (64_000, 140_000)]


def test_analyze_types_configurable() -> None:
    t = tl((10_000, W), (10_000, C), (10_000, W))
    assert spans(plan_segments(t, 30_000, analyze_types=("main_wide", "close_up"))) == [(0, 30_000)]

def test_segment_frames_half_open() -> None:
    a, b = Segment(index=0, t0_ms=0, t1_ms=2000), Segment(index=1, t0_ms=2000, t1_ms=4040)
    assert segment_frames(a) == (0, 50) and segment_frames(b) == (50, 101)


@pytest.mark.parametrize(("t0", "t1", "target"), [(0, 250_000, 120_000), (1_240, 361_000, 120_000),
                                                  (0, 10_000, 1_000), (0, 9_000, 3_000)])
def test_split_points(t0: int, t1: int, target: int) -> None:
    cuts = split_points(t0, t1, target, 2000)
    edges = [t0, *cuts, t1]
    assert all(c % 2000 == 0 for c in cuts)
    assert all(b > a for a, b in itertools.pairwise(edges))
    assert all(b - a <= max(target, 2000) for a, b in itertools.pairwise(edges))


# ------------------------------------------------------------------ properties
def random_timeline(rng: random.Random) -> ShotTimeline:
    kinds = [W, W, W, C, R, OT]
    spec = []
    for _ in range(rng.randint(1, 40)):
        frames = rng.choice([rng.randint(1, 40), rng.randint(25, 1500), rng.randint(1500, 9000)])
        spec.append((frames * FRAME, rng.choice(kinds)))
    return tl(*spec)


@pytest.mark.parametrize("seed", range(300))
def test_properties(seed: int) -> None:
    rng = random.Random(seed)
    t = random_timeline(rng)
    end = t.shots[-1].t1_ms
    duration = end if rng.random() < 0.7 else rng.randint(1, end // FRAME) * FRAME
    target = rng.choice([4_000, 10_000, 30_000, 120_000, 1_000])
    min_ms = rng.choice([0, 1000, 3000])
    segs = plan_segments(t, duration, target_ms=target, min_shot_ms=min_ms, split_ms=2000)

    # contiguous indices, sorted, disjoint, half-open, within [0, duration)
    assert [s.index for s in segs] == list(range(len(segs)))
    for a, b in itertools.pairwise(segs):
        assert a.t1_ms <= b.t0_ms
    assert all(0 <= s.t0_ms < s.t1_ms <= duration for s in segs)
    # frame grid
    assert all(s.t0_ms % FRAME == 0 and s.t1_ms % FRAME == 0 for s in segs)

    # expected coverage: analysable runs (after the duration clip) that are ≥ min_ms
    wide = [(s.t0_ms, min(s.t1_ms, duration)) for s in t.shots if s.type == W and s.t0_ms < duration]
    runs: list[list[int]] = []
    for a, b in wide:
        if runs and runs[-1][1] == a:
            runs[-1][1] = b
        else:
            runs.append([a, b])
    runs = [r for r in runs if r[1] - r[0] >= min_ms]
    covered: list[list[int]] = []
    for s in segs:
        if covered and covered[-1][1] == s.t0_ms:
            covered[-1][1] = s.t1_ms
        else:
            covered.append([s.t0_ms, s.t1_ms])
    # adjacent segments inside one run merge back into exactly that run (and nothing else is covered)
    assert covered == runs

    shot_edges = {s.t0_ms for s in t.shots} | {min(s.t1_ms, duration) for s in t.shots}
    for s in segs:
        # each segment inside one run, ≤ target unless the bound is unreachable at keyframe granularity
        assert any(r[0] <= s.t0_ms and s.t1_ms <= r[1] for r in runs)
        assert s.t1_ms - s.t0_ms <= max(target, 2000)
        for edge in (s.t0_ms, s.t1_ms):
            if edge in shot_edges:
                continue
            # a cut inside a shot: on a keyframe, and only because that shot alone exceeds the target
            assert edge % 2000 == 0
            shot = next(x for x in t.shots if x.t0_ms < edge < x.t1_ms)
            assert min(shot.t1_ms, duration) - shot.t0_ms > target

    # greedy merge: two neighbouring segments in the same run could not have been one segment
    for a, b in itertools.pairwise(segs):
        if a.t1_ms == b.t0_ms and a.t1_ms in shot_edges:
            assert b.t1_ms - a.t0_ms > target or (b.t1_ms not in shot_edges)
