"""S9 stats, ball part (M3-CV-02 / FR-406, FR-501, FR-505): ball + players in ``tracks.parquet`` → possession
spells, passes, team possession.

Measured on the 25 fps rows of the main camera only (the ball and the players need a usable H: ``h_quality ≥
MIN_Q``), and never across a shot cut (a cut breaks every sequence).

- Ball cleaning: the detector also fires on boots, socks, lines (on the ESP–CZE highlights 10 % of
  frame-to-frame ball steps were > 19 m). Detections are chained while the implied speed stays ≤ ``BALL_MAX_MS``
  (+ ``BALL_SLACK_M``); chains shorter than ``BALL_MIN_RUN_S`` are dropped.
- Contact (FR-406): in a frame with a mapped ball, the nearest player / goalkeeper of a team within ``CONTACT_M``
  (2 m) of the ball's ground point.
- Spell (``find_spells``): contacts of ONE track at most ``GAP_S`` (0.4 s) apart, spanning ≥ ``HOLD_S`` (0.4 s)
  with ≥ 3 contacts; overlapping spells of two players (a duel) go to the one with more contacts. Shorter brushes
  (a deflection, a player the ball passes by) are not possession.
- Duels (``drop_duels``): a spell of the other team shorter than ``DUEL_S`` between two spells of one team is a
  contest the team won, not two turnovers.
- Between two spells of the same shot at most ``MAX_PASS_S`` apart (FR-505): same track → the same possession;
  same team → a completed pass (ball travel ≥ ``MIN_PASS_M``, else two players crowding the ball, nothing);
  other team → a lost ball (an intercepted / failed pass, a tackle — broadcast can't tell them apart).
- Team possession time: a team owns its spells and the transit of its completed passes; the transit of a lost ball
  is contested (nobody's). Possession % = A / (A + B). Thirds = by the ball's x on the pitch as filmed
  (left / middle / right — halves and attacking directions come with the match clock, M2); a per-minute timeline
  for "possession over time" (SCR-20a E03).
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np
from cv_core.io.parquet import CLS_BALL, CLS_GOALKEEPER, CLS_PLAYER, read_tracks
from pitchvision_schemas import ShotTimeline

MIN_Q = 0.5
CONTACT_M = 2.5      # FR-406 says 2 m; ball + player ground points each carry ~0.5–0.7 m of projection noise
HOLD_S = 0.4
GAP_S = 0.4
MIN_CONTACTS = 3
DUEL_S = 0.8
MAX_PASS_S = 5.0
MIN_PASS_M = 2.0
BALL_MAX_MS = 40.0       # a struck ball tops out ~35 m/s; anything faster between frames is another object
BALL_SLACK_M = 1.0       # + projection noise per link
BALL_MIN_RUN_S = 0.2     # ball tracklets shorter than this are detector glitches
PITCH_L = 105.0
THIRDS = ("left", "middle", "right")
BUCKET_MS = 60_000
MAX_PASSES_STORED = 5000


@dataclass(frozen=True)
class Spell:
    track_id: int
    team: int
    f0: int
    f1: int              # last frame (inclusive)
    shot: int
    x0: float            # ball at the start / end (pitch metres)
    y0: float
    x1: float
    y1: float


@dataclass(frozen=True)
class Pass:
    t_ms: int            # ball leaves the passer (end of his spell)
    end_ms: int          # next spell starts
    team: int            # the passer's team
    from_track: int
    to_track: int
    completed: bool
    x0: float
    y0: float
    x1: float
    y1: float

    def row(self) -> dict[str, Any]:
        return {"t_ms": self.t_ms, "end_ms": self.end_ms, "from": self.from_track, "to": self.to_track,
                "completed": self.completed, "x0": round(self.x0, 1), "y0": round(self.y0, 1),
                "x1": round(self.x1, 1), "y1": round(self.y1, 1)}  # fmt: skip


@dataclass
class PossessionResult:
    spells: list[Spell]
    passes: list[Pass]
    team: dict[int, dict[str, Any]]                  # cluster → team metrics (stats_team.metrics)
    track: dict[int, dict[str, Any]] = field(default_factory=dict)  # track_id → per-track additions
    stats: dict[str, Any] = field(default_factory=dict)


def _shot_index(frames: np.ndarray, timeline: ShotTimeline | None, fps: float) -> np.ndarray:
    if timeline is None or not timeline.shots:
        return np.zeros(len(frames), np.int64)
    starts = np.array([math.floor(s.t0_ms * fps / 1000 + 0.5) for s in timeline.shots])
    return np.searchsorted(starts, frames, side="right") - 1


def clean_ball(frames: np.ndarray, x: np.ndarray, y: np.ndarray, fps: float) -> np.ndarray:
    """Mask of ball detections that belong to a plausible trajectory (module docstring). Sorted by frame."""
    keep = np.zeros(len(frames), bool)
    min_run = max(2, round(BALL_MIN_RUN_S * fps))
    start = 0
    for i in range(1, len(frames) + 1):
        if i < len(frames):
            gap = frames[i] - frames[i - 1]
            if 0 < gap <= round(fps) and math.hypot(x[i] - x[i - 1], y[i] - y[i - 1]) <= \
                    BALL_MAX_MS * gap / fps + BALL_SLACK_M:  # fmt: skip
                continue
        if i - start >= min_run:
            keep[start:i] = True
        start = i
    return keep


def find_spells(frames: np.ndarray, track: np.ndarray, team: np.ndarray, shot: np.ndarray, bx: np.ndarray,
                by: np.ndarray, fps: float) -> list[Spell]:  # fmt: skip
    """Contacts (one per frame, sorted by frame) → spells, time-ordered and non-overlapping.

    Per track (and shot): contacts ≤ ``GAP_S`` apart form a cluster (the nearest player flickers between two
    who stand together, the ball distance wobbles around 2 m); a cluster spanning ≥ ``HOLD_S`` with
    ≥ ``MIN_CONTACTS`` contacts is a spell. Overlapping spells (a duel): the one with more contacts keeps the
    overlap; the other is trimmed and dropped if it no longer lasts ``HOLD_S``."""
    gap, hold = round(GAP_S * fps), round(HOLD_S * fps)
    cands: list[tuple[int, Spell]] = []  # (contacts, spell)
    by_track: dict[tuple[int, int], list[int]] = defaultdict(list)
    for k in range(len(frames)):
        by_track[(int(track[k]), int(shot[k]))].append(k)
    for (tid, sh), ks in by_track.items():
        start = 0
        for n in range(1, len(ks) + 1):
            if n < len(ks) and frames[ks[n]] - frames[ks[n - 1]] <= gap + 1:
                continue
            a, b = ks[start], ks[n - 1]
            if frames[b] - frames[a] + 1 >= hold and n - start >= MIN_CONTACTS:
                cands.append((n - start, Spell(tid, int(team[a]), int(frames[a]), int(frames[b]), sh, float(bx[a]),
                                               float(by[a]), float(bx[b]), float(by[b]))))  # fmt: skip
            start = n
    out: list[Spell] = []
    for _, sp in sorted(cands, key=lambda c: -c[0]):  # strongest first claim their time
        f0, f1 = sp.f0, sp.f1
        for o in out:
            if o.f1 < f0 or o.f0 > f1:
                continue
            if o.f0 <= f0 <= o.f1:
                f0 = o.f1 + 1
            if o.f0 <= f1 <= o.f1:
                f1 = o.f0 - 1
            if f0 < o.f0 and f1 > o.f1:  # it straddles a stronger spell: keep the longer side
                f0, f1 = (f0, o.f0 - 1) if o.f0 - f0 >= f1 - o.f1 else (o.f1 + 1, f1)
        if f1 - f0 + 1 >= hold:
            out.append(Spell(sp.track_id, sp.team, f0, f1, sp.shot, sp.x0, sp.y0, sp.x1, sp.y1))
    return sorted(out, key=lambda sp: sp.f0)


def drop_duels(spells: list[Spell], fps: float) -> list[Spell]:
    """A short spell of the other team sandwiched between two spells of one team within ``MAX_PASS_S`` is a duel
    the team came out of with the ball, not two changes of possession."""
    out = list(spells)
    changed = True
    while changed:
        changed = False
        for i in range(1, len(out) - 1):
            a, m, b = out[i - 1], out[i], out[i + 1]
            if (a.team == b.team != m.team and a.shot == m.shot == b.shot
                    and (m.f1 - m.f0 + 1) / fps < DUEL_S and (b.f0 - a.f1) / fps <= MAX_PASS_S):  # fmt: skip
                del out[i]
                changed = True
                break
    return out


def run_possession(tracks: Path, timeline: ShotTimeline | None, fps: float) -> PossessionResult:
    t = read_tracks(tracks)
    c = {k: t.column(k).to_numpy(zero_copy_only=False) for k in ("frame_idx", "track_id", "cls", "team", "px",
                                                                "py", "h_quality")}  # fmt: skip
    with np.errstate(invalid="ignore"):
        mapped = np.isfinite(c["px"]) & np.isfinite(c["py"]) & (np.nan_to_num(
            c["h_quality"].astype(np.float32), nan=-1.0) >= MIN_Q)  # fmt: skip
    ball = mapped & (c["cls"] == CLS_BALL)
    people = (mapped & np.isin(c["cls"], (CLS_PLAYER, CLS_GOALKEEPER)) & (c["track_id"] >= 0)
              & np.isin(c["team"], (0, 1)))  # fmt: skip
    bf = c["frame_idx"][ball].astype(np.int64)
    order = np.argsort(bf, kind="stable")
    bf, bxs, bys = bf[order], c["px"][ball][order].astype(float), c["py"][ball][order].astype(float)
    _, first = np.unique(bf, return_index=True)  # one ball per frame (S5 keeps one; be safe)
    bf, bxs, bys = bf[first], bxs[first], bys[first]
    raw_ball = len(bf)
    good = clean_ball(bf, bxs, bys, fps)
    bf, bxs, bys = bf[good], bxs[good], bys[good]

    pf = c["frame_idx"][people].astype(np.int64)
    po = np.argsort(pf, kind="stable")
    pf, ptid = pf[po], c["track_id"][people][po].astype(np.int64)
    pteam = c["team"][people][po].astype(np.int64)
    ppx, ppy = c["px"][people][po].astype(float), c["py"][people][po].astype(float)
    lo, hi = np.searchsorted(pf, bf, "left"), np.searchsorted(pf, bf, "right")

    cf, ct, cteam, cbx, cby = [], [], [], [], []
    for k in range(len(bf)):
        a, b = lo[k], hi[k]
        if b <= a:
            continue
        d = np.hypot(ppx[a:b] - bxs[k], ppy[a:b] - bys[k])
        j = int(np.argmin(d))
        if d[j] <= CONTACT_M:
            cf.append(bf[k])
            ct.append(ptid[a + j])
            cteam.append(pteam[a + j])
            cbx.append(bxs[k])
            cby.append(bys[k])
    frames = np.array(cf, np.int64)
    shot = _shot_index(frames, timeline, fps)
    spells = find_spells(frames, np.array(ct, np.int64), np.array(cteam, np.int64), shot, np.array(cbx),
                         np.array(cby), fps)  # fmt: skip
    found = len(spells)
    spells = drop_duels(spells, fps)

    ms = lambda f: round(f * 1000 / fps)  # noqa: E731
    owned: dict[int, list[tuple[int, int, float]]] = defaultdict(list)  # team → [(f0, f1 excl., ball x)]
    passes: list[Pass] = []
    track: dict[int, dict[str, Any]] = defaultdict(lambda: {"touches": 0, "possession_s": 0.0, "passes": 0,
                                                           "passes_completed": 0, "received": 0})  # fmt: skip
    for s in spells:
        owned[s.team].append((s.f0, s.f1 + 1, (s.x0 + s.x1) / 2))
        tr = track[s.track_id]
        tr["touches"] += 1
        tr["possession_s"] += (s.f1 + 1 - s.f0) / fps
    for a, b in pairwise(spells):
        if a.shot != b.shot or (b.f0 - a.f1) / fps > MAX_PASS_S or a.track_id == b.track_id:
            continue
        travel = math.hypot(b.x0 - a.x1, b.y0 - a.y1)
        if a.team == b.team:
            if travel < MIN_PASS_M:
                continue
            owned[a.team].append((a.f1 + 1, b.f0, (a.x1 + b.x0) / 2))  # the ball in flight is still theirs
            completed = True
        else:
            completed = False
        passes.append(Pass(ms(a.f1), ms(b.f0), a.team, a.track_id, b.track_id, completed, a.x1, a.y1, b.x0, b.y0))
        track[a.track_id]["passes"] += 1
        if completed:
            track[a.track_id]["passes_completed"] += 1
            track[b.track_id]["received"] += 1

    team: dict[int, dict[str, Any]] = {}
    total = sum(f1 - f0 for spans in owned.values() for f0, f1, _ in spans)
    for cl in (0, 1):
        spans = owned.get(cl, [])
        mine = sum(f1 - f0 for f0, f1, _ in spans)
        thirds = dict.fromkeys(THIRDS, 0)
        timeline_ms: dict[int, int] = defaultdict(int)
        for f0, f1, x in spans:
            thirds[THIRDS[min(2, max(0, int(x // (PITCH_L / 3))))]] += ms(f1) - ms(f0)
            for m0 in range(ms(f0) // BUCKET_MS * BUCKET_MS, ms(f1), BUCKET_MS):  # split across minute buckets
                timeline_ms[m0 // BUCKET_MS] += min(ms(f1), m0 + BUCKET_MS) - max(ms(f0), m0)
        tp = [p for p in passes if p.team == cl]
        team[cl] = {
            "possession_ms": ms(mine), "possession_pct": round(100 * mine / total, 1) if total else None,
            "thirds_ms": thirds, "timeline_ms": dict(sorted(timeline_ms.items())),
            "passes": len(tp), "passes_completed": sum(p.completed for p in tp),
            "balls_lost": sum(not p.completed for p in tp),
            "pass_list": [p.row() for p in tp[:MAX_PASSES_STORED]],
            "version": "pv-possession/v1",
        }  # fmt: skip
    stats = {"ball_frames_raw": raw_ball, "ball_frames": len(bf), "contacts": len(frames), "spells": len(spells),
             "duels_dropped": found - len(spells),
             "passes": len(passes), "owned_s": round(total / fps, 1)}  # fmt: skip
    return PossessionResult(spells=spells, passes=passes, team=team,
                            track={k: {**v, "possession_s": round(v["possession_s"], 2)} for k, v in track.items()},
                            stats=stats)  # fmt: skip
