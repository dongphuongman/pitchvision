"""S8 load-db, pure part (AD §9.3, M1-BE-14): ``tracks.parquet`` + team model → rows for ``positions`` (5 Hz),
``teams`` and ``players``. The DB writer (``pitchvision_worker.results_db``) runs in the task layer.

- positions: every ``target_fps / POSITIONS_HZ``-th frame (25 → 5 Hz: ``ts_ms`` multiple of 200), all tracked
  objects + the ball (``track_id -1``); ``px, py`` metres (NULL without homography), ``vx, vy`` m/s from the
  previous 5 Hz sample of the same track (NULL for its first sample, across holes, or above ``MAX_SPEED_MS`` for
  people — AD §6.4 outlier rule), ``q`` = the frame's ``h_quality``;
- teams: one per team-model cluster (0, 1) with a default name and the cluster's mean shirt colour (S3 stores it in
  ``team_model.json`` ``stats.colors``; a palette colour when missing);
- players: one per global track (M1: no ReID yet, so one real player may have several rows) with its team cluster
  and role from the majority class.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from cv_core.io.parquet import CLS_BALL, CLS_GOALKEEPER, CLS_REFEREE, NO_TRACK, read_tracks
from pitchvision_schemas import PipelineConfig

POSITIONS_HZ = 5
MAX_SPEED_MS = 12.0
TEAM_NAMES = {0: "Team A", 1: "Team B"}
DEFAULT_COLORS = {0: "#e53935", 1: "#1e88e5"}
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
POSITION_COLUMNS = ("ts_ms", "track_id", "team", "px", "py", "vx", "vy", "q")


@dataclass(frozen=True)
class TeamRow:
    cluster: int
    name: str
    color: str


@dataclass(frozen=True)
class PlayerRow:
    track_id: int
    cluster: int | None
    role: str


@dataclass
class LoadBatch:
    positions: dict[str, np.ndarray]     # POSITION_COLUMNS, sorted by (ts_ms, track_id); NaN = NULL
    teams: list[TeamRow]
    players: list[PlayerRow]
    stats: dict[str, Any] = field(default_factory=dict)


def team_rows(team_model: Mapping[str, Any] | None) -> list[TeamRow]:
    """Teams from ``team_model.json`` (clusters 0 and 1; colours from ``stats.colors`` when valid)."""
    colors = ((team_model or {}).get("stats") or {}).get("colors") or {}
    out = []
    for cluster in (0, 1):
        c = colors.get(str(cluster))
        out.append(TeamRow(cluster, TEAM_NAMES[cluster], c.lower() if isinstance(c, str) and _HEX.match(c)
                           else DEFAULT_COLORS[cluster]))  # fmt: skip
    return out


def load_team_model(path: Path | None) -> dict[str, Any] | None:
    if path is None or not Path(path).is_file():
        return None
    try:
        data = json.loads(Path(path).read_text())
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _role(cls: int) -> str:
    return {CLS_GOALKEEPER: "goalkeeper", CLS_REFEREE: "referee"}.get(cls, "player")


def prepare_load(tracks: Path, team_model: Path | Mapping[str, Any] | None, config: PipelineConfig | None = None
                 ) -> LoadBatch:
    cfg = config or PipelineConfig()
    tm = team_model if isinstance(team_model, Mapping) or team_model is None else load_team_model(team_model)
    t = read_tracks(tracks)
    col = {c: t.column(c).to_numpy(zero_copy_only=False) for c in ("frame_idx", "ts_ms", "track_id", "cls", "team",
                                                                  "px", "py", "h_quality")}  # fmt: skip
    step = max(1, round(cfg.target_fps / POSITIONS_HZ))
    keep = col["frame_idx"] % step == 0
    sub = {c: v[keep] for c, v in col.items()}
    order = np.lexsort((sub["ts_ms"], sub["track_id"]))  # by track, then time (for velocities)
    sub = {c: v[order] for c, v in sub.items()}
    px, py = sub["px"].astype(np.float64), sub["py"].astype(np.float64)
    tid, ts = sub["track_id"].astype(np.int64), sub["ts_ms"].astype(np.int64)
    dt_ms = 1000 * step / cfg.target_fps
    prev_ok = np.r_[False, (tid[1:] == tid[:-1]) & (np.abs(np.diff(ts) - dt_ms) < 1)]
    vx, vy = np.full(len(tid), np.nan), np.full(len(tid), np.nan)
    if len(tid) > 1:
        vx[1:] = np.diff(px) / (dt_ms / 1000)
        vy[1:] = np.diff(py) / (dt_ms / 1000)
    vx[~prev_ok], vy[~prev_ok] = np.nan, np.nan
    with np.errstate(invalid="ignore"):
        fast = (np.hypot(vx, vy) > MAX_SPEED_MS) & (sub["cls"] != CLS_BALL)
    vx[fast], vy[fast] = np.nan, np.nan
    q = sub["h_quality"].astype(np.float64)
    positions = {"ts_ms": ts, "track_id": tid, "team": sub["team"].astype(np.int64), "px": px, "py": py,
                 "vx": vx, "vy": vy, "q": q}
    final = np.lexsort((positions["track_id"], positions["ts_ms"]))
    positions = {c: v[final] for c, v in positions.items()}

    players: list[PlayerRow] = []
    tracked = col["track_id"] != NO_TRACK
    if tracked.any():
        all_ids = col["track_id"][tracked].astype(np.int64)
        uniq, first = np.unique(all_ids, return_index=True)
        cls_t, team_t = col["cls"][tracked], col["team"][tracked]  # constant per global track after S5
        for track_id, i in zip(uniq.tolist(), first.tolist(), strict=True):
            team = int(team_t[i])
            players.append(PlayerRow(int(track_id), team if team in (0, 1) else None, _role(int(cls_t[i]))))
    stats = {"positions": len(final), "players": len(players), "step_frames": step,
             "with_xy": int(np.isfinite(positions["px"]).sum())}
    return LoadBatch(positions=positions, teams=team_rows(tm), players=players, stats=stats)
