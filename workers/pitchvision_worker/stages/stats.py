"""S9 stats, physical part (M3-CV-01 / FR-502, FR-504; AD §6.2): ``tracks.parquet`` → per-track metrics.

Computed per global track (M1–M2: a track is a player's stretch inside one shot; ReID joins them later) on the
25 fps rows, because the 5 Hz ``positions`` table is too coarse for sprints. The API sums tracks into teams at read
time with the CURRENT ``players`` team / role, so later goalkeeper / referee / team fixes count without a re-run.

- Observed samples: tracked people (players, goalkeepers, referees; never the ball) with finite ``px, py`` and
  ``h_quality ≥ MIN_Q`` (the ``homography_ok`` bar). A gap (missing frame or a weak H) splits the track into runs;
  runs shorter than ``MIN_RUN_S`` are dropped (too short to tell motion from noise).
- Smoothing: Gaussian, σ = ``SMOOTH_S`` (0.3 s), on px / py per run — box-bottom jitter on a broadcast frame is
  ~0.5 m and would otherwise add kilometres. Speed = |Δp| · fps on the smoothed path; steps faster than
  ``MAX_SPEED_MS`` (10.5 m/s = 37.8 km/h, above any footballer) are discarded: a jump of H or a track swap.
- Distance = Σ kept steps; per speed zone (FR-502): walk < 7.2, jog 7.2–14.4, run 14.4–25.2, sprint ≥ 25.2 km/h
  (the usual 2 / 4 / 7 m/s bands). Sprints = episodes ≥ 25.2 km/h held ≥ ``SPRINT_MIN_S``. Top speed = the maximum
  of the speed averaged over ``TOP_SPEED_S`` (a single-frame spike is not a top speed), with its time.
- Average position (FR-504) = mean px / py over observed samples + their covariance (the API / web draw an ellipse).
- Measured on the ESP–CZE highlights (2026-10-07): 731 tracks, 67 observed player-minutes in 0.2 s; ~180 m per
  observed player-minute, 14 % of it sprinting — highlight reels are the intense moments (a match averages
  100–130 m/min, peak minutes reach 180–200). Raising the H bar or the smoothing barely moves it (0.8 → 174,
  σ 1 s → 159), so it isn't jitter; validation against official numbers is M3-CV-04.
- ``observed_s`` = observed samples / fps: everything is "over the observed time"; the API turns it into coverage
  and labels any extrapolation ``estimated`` (M3: "mọi con số kèm độ phủ").
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np
from cv_core.io.parquet import CLS_BALL, NO_TRACK, read_tracks

MIN_Q = 0.5
MIN_RUN_S = 1.0
SMOOTH_S = 0.3
MAX_SPEED_MS = 10.5  # 37.8 km/h: above the fastest footballers; S8 positions keep the looser 12 m/s
ZONES_KMH = (("walk", 0.0), ("jog", 7.2), ("run", 14.4), ("sprint", 25.2))  # lower bounds
SPRINT_KMH = 25.2
SPRINT_MIN_S = 1.0
TOP_SPEED_S = 0.5
STATS_VERSION = "pv-stats/v1"


@dataclass(frozen=True)
class TrackStats:
    track_id: int
    observed_s: float
    distance_m: float
    zones_m: dict[str, float]          # walk / jog / run / sprint metres
    top_speed_kmh: float | None
    top_speed_ms: int | None           # video time (ms) of the top speed
    sprints: int
    avg_x: float | None                # pitch metres
    avg_y: float | None
    cov: tuple[float, float, float] | None   # (σxx², σyy², σxy) m²

    def metrics(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("track_id")
        d["cov"] = list(self.cov) if self.cov else None
        d["version"] = STATS_VERSION
        return d


def _gauss(x: np.ndarray, sigma: float) -> np.ndarray:
    if sigma <= 0 or len(x) < 3:
        return x
    r = min(max(1, round(3 * sigma)), len(x) - 1)  # a short run: a truncated kernel (pad ≤ len - 1)
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    # odd reflection continues a straight line past the ends: no distance lost at every run's start / end
    pad = np.pad(x, r, mode="reflect", reflect_type="odd")
    return np.convolve(pad, k, mode="valid")


def _runs(frames: np.ndarray) -> list[tuple[int, int]]:
    """[start, end) index ranges of consecutive frames."""
    if not len(frames):
        return []
    cut = np.flatnonzero(np.diff(frames) != 1) + 1
    edges = np.r_[0, cut, len(frames)]
    return [(int(a), int(b)) for a, b in pairwise(edges)]


def _episodes(mask: np.ndarray) -> list[tuple[int, int]]:
    if not mask.any():
        return []
    d = np.diff(np.r_[0, mask.astype(np.int8), 0])
    return list(zip(np.flatnonzero(d == 1).tolist(), np.flatnonzero(d == -1).tolist(), strict=True))


def track_stats(track_id: int, frames: np.ndarray, ts_ms: np.ndarray, px: np.ndarray, py: np.ndarray,
                fps: float) -> TrackStats:  # fmt: skip
    """Metrics of one track from its observed samples (sorted by frame; already filtered on q / finiteness)."""
    zones = {name: 0.0 for name, _ in ZONES_KMH}
    dist = observed = 0.0
    sprints = 0
    top: tuple[float, int] | None = None
    min_len = max(2, round(MIN_RUN_S * fps))
    sig, win = SMOOTH_S * fps, max(1, round(TOP_SPEED_S * fps))
    used_x: list[np.ndarray] = []
    used_y: list[np.ndarray] = []
    for a, b in _runs(frames):
        if b - a < min_len:
            continue
        x, y = _gauss(px[a:b], sig), _gauss(py[a:b], sig)
        used_x.append(px[a:b])
        used_y.append(py[a:b])
        observed += (b - a) / fps
        step = np.hypot(np.diff(x), np.diff(y))
        speed = step * fps  # m/s for the step k → k+1
        ok = speed <= MAX_SPEED_MS
        dist += float(step[ok].sum())
        kmh = speed * 3.6
        for i, (name, lo) in enumerate(ZONES_KMH):
            hi = ZONES_KMH[i + 1][1] if i + 1 < len(ZONES_KMH) else math.inf
            zones[name] += float(step[ok & (kmh >= lo) & (kmh < hi)].sum())
        min_sprint = round(SPRINT_MIN_S * fps)
        sprints += sum(1 for s, e in _episodes(ok & (kmh >= SPRINT_KMH)) if e - s >= min_sprint)
        if len(speed) >= win:
            clean = np.where(ok, speed, np.nan)
            sums = np.convolve(np.nan_to_num(clean), np.ones(win), "valid")
            good = np.convolve(ok.astype(float), np.ones(win), "valid") == win  # no outlier inside the window
            if good.any():
                avg = np.where(good, sums / win, -1.0)
                j = int(np.argmax(avg))
                if top is None or avg[j] > top[0]:
                    top = (float(avg[j]), int(ts_ms[a + j + win // 2]))
    avg_x: float | None = None
    avg_y: float | None = None
    cov: tuple[float, float, float] | None = None
    if used_x:
        ax, ay = np.concatenate(used_x), np.concatenate(used_y)
        avg_x, avg_y = float(ax.mean()), float(ay.mean())
        c = np.cov(np.vstack([ax, ay])) if len(ax) > 1 else np.zeros((2, 2))
        cov = (float(c[0, 0]), float(c[1, 1]), float(c[0, 1]))
    r = lambda v: round(v, 1)  # noqa: E731 — decimetres are plenty
    return TrackStats(track_id=track_id, observed_s=round(observed, 2), distance_m=r(dist),
                      zones_m={k: r(v) for k, v in zones.items()},
                      top_speed_kmh=round(top[0] * 3.6, 1) if top else None, top_speed_ms=top[1] if top else None,
                      sprints=sprints, avg_x=None if avg_x is None else round(avg_x, 2),
                      avg_y=None if avg_y is None else round(avg_y, 2),
                      cov=None if cov is None else tuple(round(v, 2) for v in cov))  # type: ignore[arg-type]


def run_stats(tracks: Path, fps: float) -> list[TrackStats]:
    """Every tracked person with at least one observed run (see module docstring)."""
    t = read_tracks(tracks)
    c = {k: t.column(k).to_numpy(zero_copy_only=False) for k in ("frame_idx", "ts_ms", "track_id", "cls", "px",
                                                                "py", "h_quality")}  # fmt: skip
    with np.errstate(invalid="ignore"):
        keep = ((c["track_id"] != NO_TRACK) & (c["cls"] != CLS_BALL) & np.isfinite(c["px"]) & np.isfinite(c["py"])
                & (np.nan_to_num(c["h_quality"].astype(np.float32), nan=-1.0) >= MIN_Q))  # fmt: skip
    tid = c["track_id"][keep].astype(np.int64)
    order = np.lexsort((c["frame_idx"][keep], tid))
    tid = tid[order]
    f = c["frame_idx"][keep].astype(np.int64)[order]
    ts = c["ts_ms"][keep].astype(np.int64)[order]
    px = c["px"][keep].astype(np.float64)[order]
    py = c["py"][keep].astype(np.float64)[order]
    out: list[TrackStats] = []
    if not len(tid):
        return out
    bounds = np.r_[0, np.flatnonzero(np.diff(tid)) + 1, len(tid)]
    for a, b in pairwise(bounds):
        s = track_stats(int(tid[a]), f[a:b], ts[a:b], px[a:b], py[a:b], fps)
        if s.observed_s > 0:
            out.append(s)
    return out
