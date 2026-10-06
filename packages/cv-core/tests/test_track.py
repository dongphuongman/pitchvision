"""ByteTrack + assignment on synthetic moving boxes."""
from __future__ import annotations

import itertools
import random

import numpy as np
import pytest
from cv_core.track import ByteTrackConfig, ByteTracker, hungarian, linear_assignment


# ------------------------------------------------------------------ assignment
def _brute(cost: np.ndarray, thresh: float) -> float:
    """Best total with unmatched rows/cols at thresh/2 each (the lapjv extend_cost semantics)."""
    n, m = cost.shape
    best = (n + m) * thresh / 2
    for k in range(1, min(n, m) + 1):
        for rows in itertools.combinations(range(n), k):
            for cols in itertools.permutations(range(m), k):
                if all(cost[r, c] < thresh for r, c in zip(rows, cols, strict=True)):
                    tot = sum(cost[r, c] for r, c in zip(rows, cols, strict=True)) + (n + m - 2 * k) * thresh / 2
                    best = min(best, tot)
    return best


@pytest.mark.parametrize("seed", range(40))
def test_linear_assignment_optimal(seed: int) -> None:
    rng = np.random.default_rng(seed)
    n, m = int(rng.integers(0, 5)), int(rng.integers(0, 5))
    cost = rng.random((n, m))
    thresh = 0.6
    matches, ur, uc = linear_assignment(cost, thresh)
    assert sorted([*matches[:, 0].tolist(), *ur.tolist()]) == list(range(n))
    assert sorted([*matches[:, 1].tolist(), *uc.tolist()]) == list(range(m))
    assert all(cost[i, j] < thresh for i, j in matches)
    got = sum(cost[i, j] for i, j in matches) + (n + m - 2 * len(matches)) * thresh / 2
    assert got == pytest.approx(_brute(cost, thresh))


def test_hungarian_square() -> None:
    c = [[4.0, 1.0, 3.0], [2.0, 0.0, 5.0], [3.0, 2.0, 2.0]]
    sol = hungarian(c)
    assert sum(c[i][sol[i]] for i in range(3)) == 5.0


# ------------------------------------------------------------------ tracking
def _boxes(centres: np.ndarray, w: float = 20, h: float = 50) -> np.ndarray:
    return np.c_[centres[:, 0] - w / 2, centres[:, 1] - h / 2, centres[:, 0] + w / 2, centres[:, 1] + h / 2]


def _run(frames: list[tuple[np.ndarray, np.ndarray]], cfg: ByteTrackConfig | None = None,
         can_init: list[np.ndarray] | None = None) -> list[dict[int, int]]:
    """→ per frame {object index: track id} (object index = position in that frame's detection list)."""
    tr = ByteTracker(25, cfg)
    out = []
    for k, (boxes, scores) in enumerate(frames):
        tracks = tr.update(boxes, scores, None if can_init is None else can_init[k])
        out.append({t.det_index: t.track_id for t in tracks})
    return out


def test_ids_consistent_for_linear_motion() -> None:
    rng = random.Random(0)
    starts = np.array([[100.0 + 120 * i, 200.0 + 30 * (i % 3)] for i in range(8)])
    vel = np.array([[rng.uniform(-3, 3), rng.uniform(-2, 2)] for _ in range(8)])
    frames = [(_boxes(starts + k * vel), np.full(8, 0.9)) for k in range(100)]
    res = _run(frames)
    ids = {res[-1][i] for i in range(8)}
    assert len(ids) == 8
    for r in res[1:]:  # from frame 2 on every object has the same id as at the end
        assert {i: r[i] for i in range(8)} == res[-1]


def test_occlusion_gap_keeps_id() -> None:
    c = lambda k: np.array([[100.0 + 4 * k, 300.0]])  # noqa: E731
    frames = []
    for k in range(60):
        if 20 <= k < 32:          # fully missed for 12 frames (< buffer 25): Kalman coasts, re-found by IoU
            frames.append((np.zeros((0, 4)), np.zeros(0)))
        else:
            frames.append((_boxes(c(k)), np.array([0.9])))
    res = _run(frames)
    assert len({r[0] for r in res if 0 in r}) == 1
    assert all(0 not in res[k] for k in range(20, 32)) and 0 in res[32]


def test_low_score_detections_continue_a_track() -> None:
    c = lambda k: np.array([[100.0 + 4 * k, 300.0]])  # noqa: E731
    scores = [0.9] * 10 + [0.2] * 10 + [0.9] * 5      # partially occluded: below the high threshold
    res = _run([(_boxes(c(k)), np.array([s])) for k, s in enumerate(scores)])
    assert len({r[0] for r in res if 0 in r}) == 1
    assert all(0 in res[k] for k in range(1, 25))


def test_lost_track_expires_and_new_track_appears() -> None:
    frames = [(_boxes(np.array([[100.0, 100.0]])), np.array([0.9]))] * 10
    frames += [(np.zeros((0, 4)), np.zeros(0))] * 80  # longer than the buffer (60 @ 30 fps → 50 frames @ 25)
    frames += [(_boxes(np.array([[100.0, 100.0]])), np.array([0.9]))] * 5
    res = _run(frames)
    assert res[5][0] != res[-1][0]


def test_new_object_gets_new_id_and_unconfirmed_needs_two_frames() -> None:
    a = np.array([[100.0, 100.0]])
    b = np.array([[600.0, 100.0]])
    frames = [(_boxes(a), np.array([0.9]))] * 5 + [(_boxes(np.r_[a, b]), np.array([0.9, 0.9]))] * 5
    res = _run(frames)
    assert 1 not in res[5]          # first sighting of b (not frame 1): unconfirmed, not reported
    assert res[6][1] != res[6][0]   # then reported with its own id
    assert res[9][1] == res[6][1]


def test_low_score_and_can_init_do_not_start_tracks() -> None:
    frames = [(_boxes(np.array([[100.0, 100.0], [400.0, 100.0]])), np.array([0.3, 0.9]))] * 6
    can = [np.array([True, False])] * 6
    res = _run(frames, can_init=can)
    assert all(r == {} for r in res)
    # but an existing track continues through a can_init=False detection (player stepping off the mask)
    frames = [(_boxes(np.array([[100.0 + 2 * k, 100.0]])), np.array([0.9])) for k in range(10)]
    can = [np.array([k < 5]) for k in range(10)]
    res = _run(frames, can_init=can)
    assert len({r[0] for r in res if 0 in r}) == 1 and 0 in res[9]


def test_crossing_players_with_noise() -> None:
    rng = np.random.default_rng(1)
    frames = []
    for k in range(80):
        p = np.array([[100.0 + 5 * k, 300.0], [500.0 - 5 * k, 304.0]])
        frames.append((_boxes(p + rng.normal(0, 1, p.shape)), np.array([0.9, 0.85])))
    res = _run(frames)
    # identities hold before and after the crossing (frame ~40); at most one swap is tolerated by ByteTrack in
    # general — this geometry (different speeds' directions, 4 px vertical offset) must not swap
    assert res[10] == res[79]
