"""Per-second shot probabilities + cut list → ``ShotTimeline`` (smoothing + merging of short shots).

1. Cuts split ``[0, duration)`` into segments; nothing is smoothed across a cut.
2. Inside a segment, per-sample probabilities are averaged over a centred window of ``smooth`` samples and the
   argmax label is taken; runs shorter than ``min_run`` samples are relabelled to the neighbouring run, then
   the segment is split where the label changes (midway between the two samples). This catches transitions
   the cut detector missed (dissolves, wipes, replay logos).
3. Pieces shorter than ``min_shot_ms`` are merged into a neighbour (same type first, else the longer one),
   shortest first; finally adjacent pieces of the same type are fused. Confidence = duration-weighted mean of
   the per-sample probability of the piece's type.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

import numpy as np
from pitchvision_schemas import Shot, ShotTimeline, ShotType

from cv_core.shots.classifier import SHOT_TYPES, ShotProbs


@dataclass
class _Piece:
    t0: int
    t1: int
    type: ShotType | None
    conf: float

    @property
    def dur(self) -> int:
        return self.t1 - self.t0


def _segment_pieces(t0: int, t1: int, ts: list[int], P: np.ndarray, smooth: int, min_run: int) -> list[_Piece]:
    if not ts:
        return [_Piece(t0, t1, None, 0.0)]
    n = len(ts)
    half = smooth // 2
    Ps = np.stack([P[max(0, i - half):i + half + 1].mean(axis=0) for i in range(n)])
    lab = Ps.argmax(axis=1)
    # relabel short runs (not touching the segment ends) to the previous run's label
    changed = True
    while changed and min_run > 1:
        changed = False
        runs = _runs(lab)
        if len(runs) <= 1:
            break
        for k, (a, b, _) in enumerate(runs):
            if b - a < min_run and 0 < k < len(runs) - 1:
                before, after = runs[k - 1], runs[k + 1]
                lab[a:b] = before[2] if before[1] - before[0] >= after[1] - after[0] else after[2]
                changed = True
                break
    pieces = []
    runs = _runs(lab)
    for k, (a, b, label) in enumerate(runs):
        p0 = t0 if k == 0 else (ts[a - 1] + ts[a]) // 2
        p1 = t1 if k == len(runs) - 1 else (ts[b - 1] + ts[b]) // 2
        if p1 > p0:
            pieces.append(_Piece(p0, p1, SHOT_TYPES[int(label)], float(P[a:b, label].mean())))
    return pieces


def _runs(lab: np.ndarray) -> list[tuple[int, int, int]]:
    out, a = [], 0
    for i in range(1, len(lab) + 1):
        if i == len(lab) or lab[i] != lab[a]:
            out.append((a, i, int(lab[a])))
            a = i
    return out


def _absorb(into: _Piece, other: _Piece) -> None:
    if into.type is None:
        into.type, into.conf = other.type, other.conf
    elif other.type == into.type:
        into.conf = (into.conf * into.dur + other.conf * other.dur) / max(into.dur + other.dur, 1)
    into.t0, into.t1 = min(into.t0, other.t0), max(into.t1, other.t1)


def build_timeline(samples: Sequence[tuple[int, ShotProbs]], cuts_ms: Sequence[int], duration_ms: int, *,
                   min_shot_ms: int = 1000, smooth: int = 3, min_run: int = 2) -> ShotTimeline:
    """``samples`` = (t_ms, probs) sorted by time; ``cuts_ms`` = shot start times (0 / duration ignored)."""
    if duration_ms <= 0:
        return ShotTimeline(shots=[])
    bounds = [0, *sorted({c for c in cuts_ms if 0 < c < duration_ms}), duration_ms]
    ts_all = [t for t, _ in samples]
    P_all = np.stack([p.probs for _, p in samples]) if samples else np.zeros((0, len(SHOT_TYPES)))
    pieces: list[_Piece] = []
    for a, b in pairwise(bounds):
        i0, i1 = np.searchsorted(ts_all, a, "left"), np.searchsorted(ts_all, b, "left")
        pieces += _segment_pieces(a, b, ts_all[i0:i1], P_all[i0:i1], smooth, min_run)

    # unlabelled pieces (no sample inside, i.e. short) take the neighbour's type when merged; if all are unlabelled
    if all(p.type is None for p in pieces):
        return ShotTimeline(shots=[Shot(t0_ms=0, t1_ms=duration_ms, type=ShotType.other, conf=0.0)])

    while len(pieces) > 1:
        short = [i for i, p in enumerate(pieces) if p.dur < min_shot_ms or p.type is None]
        if not short:
            break
        i = min(short, key=lambda j: (pieces[j].type is not None, pieces[j].dur))
        p = pieces[i]
        nbrs = [j for j in (i - 1, i + 1) if 0 <= j < len(pieces)]
        same = [j for j in nbrs if pieces[j].type == p.type and p.type is not None]
        typed = [j for j in nbrs if pieces[j].type is not None]
        target = max(same or typed or nbrs, key=lambda j: pieces[j].dur)
        _absorb(pieces[target], p)
        del pieces[i]

    fused: list[_Piece] = []
    for p in pieces:
        if fused and fused[-1].type == p.type:
            _absorb(fused[-1], p)
        else:
            fused.append(p)
    return ShotTimeline(shots=[
        Shot(t0_ms=p.t0, t1_ms=p.t1, type=p.type or ShotType.other, conf=round(min(1.0, max(0.0, p.conf)), 4))
        for p in fused
    ])
