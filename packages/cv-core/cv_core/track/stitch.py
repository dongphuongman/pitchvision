"""S5 stitch v1 (AD §6.4, M1-CV-13): per-segment detections → one track table with GLOBAL track ids.

Segments (``cv_core.segments``) are contiguous, non-overlapping and cut either on a shot boundary or — inside a
shot longer than the target — on a keyframe boundary. v1 rules:

1. **Link only inside a shot.** Two neighbouring segments are joined only when ``a.t1 == b.t0`` *and* that boundary
   is not a shot cut. Across shot cuts nothing is linked in M1 (that needs appearance ReID, M2).
2. **Link candidates**: tracks of ``a`` whose last frame and tracks of ``b`` whose first frame are within
   ``max_link_gap_ms`` (1 s) of each other across the boundary, same role (field player / goalkeeper vs referee),
   not of different teams (``-1`` is compatible with both), last/first box IoU ≥ ``min_link_iou``. Cost =
   ``1 − IoU`` + a small gap penalty; solved with the cost-limited Hungarian of ``cv_core.track.assignment``.
3. **Global ids** are assigned in segment order, then local id order (deterministic), starting at 1. A linked
   track keeps the id of its predecessor. Team / class of a global track = majority over its rows.
4. **Gap interpolation**: a track missing for ≤ ``max_interp_gap_ms`` (0.4 s, also fills Fast-mode stride-2 frames)
   gets linearly interpolated boxes, ``interpolated = true``; longer gaps stay gaps.
5. **Ball rows** (``track_id = -1``) are kept as they are (no ball tracking in v1).
6. **Pitch projection** (optional): with per-frame image→pitch homographies the bottom-centre of each box is
   projected to pitch metres (``px, py``; NaN without H, behind the camera or more than ``pitch_margin_m`` off the
   pitch) and the frame's ``h_quality`` is copied to the row (NaN without H).
"""
from __future__ import annotations

import itertools
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from cv_core.io.parquet import CLS_REFEREE, NO_TRACK
from cv_core.track.assignment import linear_assignment
from cv_core.track.bytetrack import iou_matrix

PITCH_LENGTH_M, PITCH_WIDTH_M = 105.0, 68.0
_DET_COLS = ("frame_idx", "track_id", "cls", "team", "x1", "y1", "x2", "y2", "conf")
_INFEASIBLE = 1e6


@dataclass(frozen=True)
class StitchConfig:
    fps: float = 25.0
    max_link_gap_ms: int = 1000      # link only if the track resumes within 1 s across the boundary
    min_link_iou: float = 0.2        # last box of a vs first box of b
    gap_penalty: float = 0.3         # added cost at the maximum gap (prefers the closest continuation)
    max_interp_gap_ms: int = 400     # interpolate holes up to 0.4 s (AD §6.5 `interpolated`)
    pitch_margin_m: float = 15.0     # projections further off the pitch are dropped (stands, bench, bad H)
    pitch_size_m: tuple[float, float] = (PITCH_LENGTH_M, PITCH_WIDTH_M)

    def frames(self, ms: float) -> int:
        return round(ms * self.fps / 1000)


@dataclass(frozen=True)
class SegmentDetections:
    """One S4 segment: its half-open time range and its ``detections.parquet`` columns (numpy arrays)."""

    index: int
    t0_ms: int
    t1_ms: int
    columns: Mapping[str, np.ndarray]


@dataclass(frozen=True)
class FrameHomographies:
    """Per-frame image→pitch homographies (NaN rows = none) and their ``h_quality``."""

    frame_idx: np.ndarray   # [N] int
    H: np.ndarray           # [N, 3, 3]
    quality: np.ndarray     # [N]

    @classmethod
    def concat(cls, parts: Iterable[FrameHomographies]) -> FrameHomographies | None:
        ps = list(parts)
        if not ps:
            return None
        return cls(np.concatenate([p.frame_idx for p in ps]).astype(np.int64),
                   np.concatenate([p.H for p in ps]).reshape(-1, 3, 3).astype(np.float64),
                   np.concatenate([p.quality for p in ps]).astype(np.float32))

    def dense(self) -> tuple[int, np.ndarray, np.ndarray]:
        """→ (first frame, H [F, 3, 3], quality [F]) over the covered frame range (NaN where missing)."""
        if not len(self.frame_idx):
            return 0, np.full((0, 3, 3), np.nan), np.full(0, np.nan, np.float32)
        f0, f1 = int(self.frame_idx.min()), int(self.frame_idx.max()) + 1
        H = np.full((f1 - f0, 3, 3), np.nan)
        q = np.full(f1 - f0, np.nan, np.float32)
        H[self.frame_idx - f0] = self.H
        q[self.frame_idx - f0] = self.quality
        bad = ~np.all(np.isfinite(H.reshape(-1, 9)), axis=1)
        q[bad] = np.nan
        return f0, H, q


@dataclass
class StitchResult:
    columns: dict[str, np.ndarray]                       # tracks.parquet columns, sorted by (frame_idx, track_id)
    links: list[tuple[int, int, int, int]] = field(default_factory=list)  # (seg a, local a, seg b, local b)
    stats: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _Ends:
    local: np.ndarray        # [T] local track ids (sorted)
    first: np.ndarray        # [T] first frame
    last: np.ndarray         # [T] last frame
    first_box: np.ndarray    # [T, 4]
    last_box: np.ndarray     # [T, 4]
    team: np.ndarray         # [T] majority team (-1 none)
    referee: np.ndarray      # [T] bool: majority class is referee


def _majority(values: np.ndarray, groups: np.ndarray, n_groups: int, choices: Sequence[int],
              default: int) -> np.ndarray:
    """Most frequent of ``choices`` per group (ties → the earlier choice; no votes → ``default``)."""
    counts = np.stack([np.bincount(groups[values == c], minlength=n_groups) for c in choices], axis=1)
    out = np.asarray(choices)[counts.argmax(1)]
    out[counts.sum(1) == 0] = default
    return out


def _ends(cols: Mapping[str, np.ndarray]) -> _Ends:
    tid = np.asarray(cols["track_id"])
    rows = np.flatnonzero(tid != NO_TRACK)
    frame = np.asarray(cols["frame_idx"])
    order = rows[np.lexsort((frame[rows], tid[rows]))]
    t = tid[order]
    if not len(t):
        z = np.zeros(0, np.int64)
        return _Ends(z, z, z, np.zeros((0, 4)), np.zeros((0, 4)), z, np.zeros(0, bool))
    starts = np.flatnonzero(np.r_[True, t[1:] != t[:-1]])
    ends = np.r_[starts[1:], len(order)] - 1
    group = np.repeat(np.arange(len(starts)), np.diff(np.r_[starts, len(order)]))
    box = np.stack([np.asarray(cols[c], np.float64) for c in ("x1", "y1", "x2", "y2")], axis=1)
    team = _majority(np.asarray(cols["team"])[order], group, len(starts), (0, 1), -1)
    cls = _majority(np.asarray(cols["cls"])[order], group, len(starts), (0, 1, 2), 0)
    return _Ends(t[starts].astype(np.int64), frame[order[starts]].astype(np.int64),
                 frame[order[ends]].astype(np.int64), box[order[starts]], box[order[ends]], team,
                 cls == CLS_REFEREE)


def link_boundary(a: _Ends, b: _Ends, boundary_frame: int, cfg: StitchConfig) -> list[tuple[int, int]]:
    """Local id pairs (a, b) continuing across ``boundary_frame`` (first frame of b's segment)."""
    max_gap = max(1, cfg.frames(cfg.max_link_gap_ms))
    ia = np.flatnonzero((a.last < boundary_frame) & (a.last >= boundary_frame - max_gap))
    ib = np.flatnonzero((b.first >= boundary_frame) & (b.first < boundary_frame + max_gap))
    if not len(ia) or not len(ib):
        return []
    iou = iou_matrix(a.last_box[ia], b.first_box[ib])
    gap = b.first[ib][None, :] - a.last[ia][:, None]
    ta, tb = a.team[ia][:, None], b.team[ib][None, :]
    ok = ((gap >= 1) & (gap <= max_gap) & (iou >= cfg.min_link_iou)
          & ~((ta >= 0) & (tb >= 0) & (ta != tb)) & (a.referee[ia][:, None] == b.referee[ib][None, :]))
    cost = np.where(ok, 1.0 - iou + cfg.gap_penalty * (gap - 1) / max_gap, _INFEASIBLE)
    matches, _, _ = linear_assignment(cost, thresh=1.0 - cfg.min_link_iou + cfg.gap_penalty + 1e-6)
    return [(int(a.local[ia[i]]), int(b.local[ib[j]])) for i, j in matches]


def _interpolate(cols: dict[str, np.ndarray], max_gap: int) -> dict[str, np.ndarray]:
    """Fill holes of ≤ ``max_gap`` frames inside each track (rows must be sorted by (track_id, frame_idx))."""
    tid, f = cols["track_id"], cols["frame_idx"]
    d = np.diff(f)
    holes = np.flatnonzero((tid[1:] == tid[:-1]) & (tid[:-1] != NO_TRACK) & (d > 1) & (d <= max_gap))
    if not len(holes) or max_gap < 2:
        return cols
    n_new = d[holes] - 1
    base = np.repeat(holes, n_new)
    step = np.concatenate([np.arange(1, k + 1) for k in n_new])
    t = step / np.repeat(d[holes], n_new)
    new: dict[str, np.ndarray] = {}
    for c, v in cols.items():
        if c in ("x1", "y1", "x2", "y2"):
            v0, v1 = v[base].astype(np.float64), v[base + 1].astype(np.float64)
            new[c] = v0 + t * (v1 - v0)
        elif c == "frame_idx":
            new[c] = f[base] + step
        elif c == "conf":
            new[c] = np.minimum(v[base], v[base + 1])
        elif c == "interpolated":
            new[c] = np.ones(len(base), bool)
        else:  # track_id, cls, team: constant along a track
            new[c] = v[base]
    return {c: np.concatenate([cols[c].astype(np.float64) if c in ("x1", "y1", "x2", "y2") else cols[c], new[c]])
            for c in cols}


def project_rows(cols: dict[str, np.ndarray], hom: FrameHomographies | None, cfg: StitchConfig,
                 chunk: int = 500_000) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Bottom-centre of every box → pitch metres through its frame's H → ``(px, py, h_quality)`` (NaN = none)."""
    n = len(cols["frame_idx"])
    px, py = np.full(n, np.nan, np.float32), np.full(n, np.nan, np.float32)
    hq = np.full(n, np.nan, np.float32)
    if hom is None or not len(hom.frame_idx) or not n:
        return px, py, hq
    f0, Hd, qd = hom.dense()
    L, W = cfg.pitch_size_m
    m = cfg.pitch_margin_m
    for s in range(0, n, chunk):
        sl = slice(s, min(n, s + chunk))
        idx = cols["frame_idx"][sl].astype(np.int64) - f0
        inside = (idx >= 0) & (idx < len(qd))
        idx = np.where(inside, idx, 0)
        H = Hd[idx]
        q = np.where(inside, qd[idx], np.nan)
        x = (cols["x1"][sl].astype(np.float64) + cols["x2"][sl]) / 2
        y = cols["y2"][sl].astype(np.float64)
        with np.errstate(invalid="ignore", divide="ignore"):
            w = H[:, 2, 0] * x + H[:, 2, 1] * y + H[:, 2, 2]
            u = (H[:, 0, 0] * x + H[:, 0, 1] * y + H[:, 0, 2]) / w
            v = (H[:, 1, 0] * x + H[:, 1, 1] * y + H[:, 1, 2]) / w
            ok = inside & np.isfinite(q) & np.isfinite(u) & np.isfinite(v) & (w > 0)
            ok &= (u >= -m) & (u <= L + m) & (v >= -m) & (v <= W + m)
        px[sl] = np.where(ok, u, np.nan)
        py[sl] = np.where(ok, v, np.nan)
        hq[sl] = q
    return px, py, hq


def stitch(segments: Sequence[SegmentDetections], *, cut_frames: Iterable[int] = (),
           homography: FrameHomographies | None = None, config: StitchConfig | None = None) -> StitchResult:
    """Merge per-segment detections into global tracks (rules in the module docstring)."""
    cfg = config or StitchConfig()
    segs = sorted(segments, key=lambda s: (s.t0_ms, s.index))
    cuts = {int(c) for c in cut_frames}
    ends = [_ends(s.columns) for s in segs]

    links: list[tuple[int, int, int, int]] = []
    prev_of: dict[tuple[int, int], tuple[int, int]] = {}  # (seg pos, local) → (seg pos, local) it continues
    skipped_cut = 0
    for k, (a, b) in enumerate(itertools.pairwise(segs)):
        if a.t1_ms != b.t0_ms:
            continue  # non-analysed footage in between = at least one cut
        boundary = cfg.frames(b.t0_ms)
        if boundary in cuts:
            skipped_cut += 1
            continue
        for la, lb in link_boundary(ends[k], ends[k + 1], boundary, cfg):
            prev_of[(k + 1, lb)] = (k, la)
            links.append((a.index, la, b.index, lb))

    gid: dict[tuple[int, int], int] = {}
    next_id = 1
    for k, e in enumerate(ends):
        for local in e.local.tolist():
            key = (k, int(local))
            if key in prev_of:
                gid[key] = gid[prev_of[key]]
            else:
                gid[key] = next_id
                next_id += 1

    parts: list[dict[str, np.ndarray]] = []
    for k, s in enumerate(segs):
        c = {name: np.asarray(s.columns[name]) for name in _DET_COLS}
        local = c["track_id"].astype(np.int64)
        keys = ends[k].local
        lut = np.array([gid[(k, int(t))] for t in keys], np.int64)
        pos = np.searchsorted(keys, local)
        is_track = local != NO_TRACK
        mapped = np.full(len(local), NO_TRACK, np.int64)
        if len(keys):
            mapped[is_track] = lut[np.clip(pos[is_track], 0, len(keys) - 1)]
        c["track_id"] = mapped
        parts.append(c)
    if parts:
        cols = {name: np.concatenate([p[name] for p in parts]) for name in _DET_COLS}
    else:
        cols = {name: np.zeros(0) for name in _DET_COLS}
    cols["frame_idx"] = cols["frame_idx"].astype(np.int64)
    cols["track_id"] = cols["track_id"].astype(np.int64)
    cols["interpolated"] = np.zeros(len(cols["frame_idx"]), bool)

    # team / class per global track (ball rows keep theirs)
    tid = cols["track_id"]
    tracked = tid != NO_TRACK
    if tracked.any():
        uniq, inv = np.unique(tid[tracked], return_inverse=True)
        team = _majority(cols["team"][tracked], inv, len(uniq), (0, 1), -1)
        cls = _majority(cols["cls"][tracked], inv, len(uniq), (0, 1, 2), 0)
        cols["team"] = cols["team"].astype(np.int64)
        cols["cls"] = cols["cls"].astype(np.int64)
        cols["team"][tracked] = team[inv]
        cols["cls"][tracked] = cls[inv]

    order = np.lexsort((cols["frame_idx"], cols["track_id"]))
    cols = {c: v[order] for c, v in cols.items()}
    n_before = len(order)
    cols = _interpolate(cols, max(1, cfg.frames(cfg.max_interp_gap_ms)))
    order = np.lexsort((cols["track_id"], cols["frame_idx"]))
    cols = {c: v[order] for c, v in cols.items()}
    px, py, hq = project_rows(cols, homography, cfg)
    cols["px"], cols["py"], cols["h_quality"] = px, py, hq
    cols["ts_ms"] = np.floor(cols["frame_idx"] * 1000.0 / cfg.fps + 0.5).astype(np.int64)

    n_tracks = next_id - 1
    stats = {"segments": len(segs), "rows": len(order), "tracks": n_tracks, "links": len(links),
             "boundaries_on_cuts": skipped_cut, "interpolated_rows": len(order) - n_before,
             "projected_rows": int(np.isfinite(px).sum()),
             "ball_rows": int((cols["track_id"] == NO_TRACK).sum())}
    return StitchResult(columns=cols, links=links, stats=stats)
