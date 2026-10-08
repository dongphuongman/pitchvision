"""Data exports (M3-BE-03, SCR-25 tab Data / SCR-20f): tracks.parquet + the frozen ``ExportSpec`` → JSON or ZIP of CSVs.

Schema ``pitchvision.data/v1``. Tracking rows (5 Hz = every 5th frame, or every frame at 25 Hz) inside
``[from_ms, to_ms)``, one per tracked object per frame:

``t_ms, frame, shot_type, track_id, team, role, x_m, y_m, h_quality, estimated, conf, box_x1, box_y1, box_x2, box_y2``

- ``shot_type``: the S2b shot the frame belongs to (``main_wide`` … ``replay``); positions exist on ``main_wide`` only.
- ``x_m, y_m``: pitch metres (0,0 = a corner flag; x along the touchline, 105 × 68), empty when the frame has no
  usable homography (``h_quality < 0.5``) — never a guessed position.
- ``estimated``: true when S5 interpolated the row (a gap ≤ 0.4 s inside a track), false when it was detected.
- ``team`` / ``role``: as detected, with the user's SCR-14 fixes applied (``track_overrides``).

Stats are the ones the user saw when exporting (``spec.stats`` = GET /stats + /passes); ``estimated_distance_m`` is
an extrapolation and is named as such. CSV = a ZIP: tracking.csv, teams.csv, players.csv, passes.csv, shots.csv,
README.txt.
"""
from __future__ import annotations

import csv
import io
import json
import math
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from cv_core.io.parquet import read_tracks
from pitchvision_schemas import ExportSpec

SCHEMA = "pitchvision.data/v1"
MIN_Q = 0.5
ROLES = {0: "player", 1: "goalkeeper", 2: "referee", 3: "ball"}
TRACK_COLUMNS = ("t_ms", "frame", "shot_type", "track_id", "team", "role", "x_m", "y_m", "h_quality", "estimated",
                 "conf", "box_x1", "box_y1", "box_x2", "box_y2")  # fmt: skip
TEAM_COLUMNS = ("cluster", "name", "color", "possession_pct", "possession_ms", "passes", "passes_completed",
                "balls_lost", "distance_observed_m", "distance_match_estimated_m", "m_per_observed_min", "sprints",
                "top_speed_kmh", "top_speed_track_id", "top_speed_t_ms", "observed_player_s", "tracks")  # fmt: skip
PLAYER_COLUMNS = ("track_id", "team", "role", "observed_s", "distance_m", "top_speed_kmh", "top_speed_t_ms",
                  "sprints", "avg_x_m", "avg_y_m")  # fmt: skip
PASS_COLUMNS = ("t_ms", "end_ms", "team", "from_track", "to_track", "completed", "x0_m", "y0_m", "x1_m", "y1_m")
README = """PitchVision data export ({schema})
Video: {title}  ·  range {from_ms}–{to_ms} ms  ·  tracking at {rate} Hz

tracking.csv  one row per tracked object per sampled frame
  shot_type   the camera shot of that frame; pitch positions exist on main_wide shots only
  x_m, y_m    pitch metres (0,0 = corner flag, 105 x 68); empty = no usable pitch mapping on that frame
  estimated   1 = interpolated across a short gap (<= 0.4 s), 0 = detected
  team/role   with the corrections made in the app applied
teams.csv     per team; distance_match_estimated_m is an ESTIMATE (observed intensity x 11 players x video time),
              everything else is measured over the observed time only (observed_player_s)
players.csv   per track (one player may have several tracks until player identities exist)
passes.csv    passes inferred from ball possession: completed = to a teammate, 0 = lost to the other team
shots.csv     the shot timeline
"""


@dataclass(frozen=True)
class DataResult:
    rows: int
    bytes: int


def _num(v: Any, nd: int = 2) -> Any:
    if v is None:
        return None
    f = float(v)
    return None if not math.isfinite(f) else round(f, nd)


def tracking_rows(spec: ExportSpec, tracks: Path) -> Iterator[list[Any]]:
    t = read_tracks(tracks)
    c = {k: t.column(k).to_numpy(zero_copy_only=False) for k in ("frame_idx", "ts_ms", "track_id", "cls", "team",
         "px", "py", "h_quality", "interpolated", "conf", "x1", "y1", "x2", "y2")}  # fmt: skip
    step = max(1, round(spec.fps / spec.rate_hz))
    keep = (c["ts_ms"] >= spec.from_ms) & (c["ts_ms"] < spec.to_ms) & (c["frame_idx"] % step == 0)
    order = np.lexsort((c["track_id"][keep], c["frame_idx"][keep]))
    sel = {k: v[keep][order] for k, v in c.items()}
    starts = np.array([s.t0_ms for s in spec.shots], np.int64)
    types = [str(s.type) for s in spec.shots]
    fix = {o.track_id: o for o in spec.track_overrides}
    for i in range(len(sel["frame_idx"])):
        ts, tid, cls = int(sel["ts_ms"][i]), int(sel["track_id"][i]), int(sel["cls"][i])
        team = int(sel["team"][i]) if int(sel["team"][i]) in (0, 1) else None
        if tid in fix:
            team = fix[tid].team if fix[tid].team in (0, 1) else None
            cls = fix[tid].cls
        k = int(np.searchsorted(starts, ts, side="right")) - 1 if len(starts) else -1
        q = float(sel["h_quality"][i])
        mapped = math.isfinite(q) and q >= MIN_Q
        yield [ts, int(sel["frame_idx"][i]), types[k] if k >= 0 else None, tid if tid >= 0 else None, team,
               ROLES.get(cls, "player"), _num(sel["px"][i]) if mapped else None,
               _num(sel["py"][i]) if mapped else None, _num(q, 3), bool(sel["interpolated"][i]),
               _num(sel["conf"][i], 3), *(_num(sel[b][i], 1) for b in ("x1", "y1", "x2", "y2"))]  # fmt: skip


def _teams(stats: dict[str, Any]) -> list[list[Any]]:
    out = []
    for t in stats.get("teams") or []:
        top = t.get("top_speed") or {}
        out.append([t.get("cluster"), t.get("name"), t.get("color"), t.get("possession_pct"), t.get("possession_ms"),
                    t.get("passes"), t.get("passes_completed"), t.get("balls_lost"), t.get("distance_m"),
                    t.get("estimated_distance_m"), t.get("m_per_min"), t.get("sprints"), top.get("kmh"),
                    top.get("track_id"), top.get("at_ms"), t.get("observed_player_s"), t.get("tracks")])  # fmt: skip
    return out


def _players(stats: dict[str, Any]) -> list[list[Any]]:
    return [[p.get("track_id"), p.get("cluster"), p.get("role"), p.get("observed_s"), p.get("distance_m"),
             p.get("top_speed_kmh"), p.get("top_speed_ms"), p.get("sprints"), p.get("avg_x"), p.get("avg_y")]
            for p in stats.get("players") or []]  # fmt: skip


def _passes(stats: dict[str, Any], spec: ExportSpec) -> list[list[Any]]:
    keys = ("t_ms", "end_ms", "cluster", "from_track", "to_track", "completed", "x0", "y0", "x1", "y1")
    return [[p[k] for k in keys] for p in stats.get("passes") or [] if spec.from_ms <= p["t_ms"] < spec.to_ms]


def _csv(columns: tuple[str, ...], rows: Any) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(columns)
    for r in rows:
        w.writerow(["" if v is None else (int(v) if isinstance(v, bool) else v) for v in r])
    return buf.getvalue().encode()


def build_data_export(spec: ExportSpec, tracks: Path, out: Path,
                      on_progress: Callable[[float], bool] = lambda _p: True) -> DataResult:  # fmt: skip
    stats = spec.stats or {}
    rows = list(tracking_rows(spec, tracks))
    on_progress(60.0)
    shots = [[s.t0_ms, s.t1_ms, str(s.type)] for s in spec.shots]
    meta = {"schema": SCHEMA, "video": {"title": spec.video_title, "duration_ms": spec.duration_ms, "fps": spec.fps,
            "width": spec.width, "height": spec.height}, "range": {"from_ms": spec.from_ms, "to_ms": spec.to_ms},
            "rate_hz": spec.rate_hz, "pitch": {"length_m": 105.0, "width_m": 68.0, "origin": "corner flag",
            "status": spec.pitch_status}, "analysis": {"job_id": spec.job_id, "revision": spec.revision}}  # fmt: skip
    if spec.data_format == "json":
        doc = {**meta, "teams": [dict(zip(TEAM_COLUMNS, r, strict=True)) for r in _teams(stats)],
               "players": [dict(zip(PLAYER_COLUMNS, r, strict=True)) for r in _players(stats)],
               "passes": [dict(zip(PASS_COLUMNS, r, strict=True)) for r in _passes(stats, spec)],
               "possession_timeline": stats.get("possession_timeline") or [],
               "shots": [dict(zip(("t0_ms", "t1_ms", "type"), s, strict=True)) for s in shots],
               "tracking": {"columns": list(TRACK_COLUMNS), "rows": rows}}  # fmt: skip
        out.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")))
    else:
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("README.txt", README.format(schema=SCHEMA, title=spec.video_title or "", from_ms=spec.from_ms,
                                                   to_ms=spec.to_ms, rate=spec.rate_hz))  # fmt: skip
            z.writestr("tracking.csv", _csv(TRACK_COLUMNS, rows))
            z.writestr("teams.csv", _csv(TEAM_COLUMNS, _teams(stats)))
            z.writestr("players.csv", _csv(PLAYER_COLUMNS, _players(stats)))
            z.writestr("passes.csv", _csv(PASS_COLUMNS, _passes(stats, spec)))
            z.writestr("shots.csv", _csv(("t0_ms", "t1_ms", "type"), shots))
            z.writestr("meta.json", json.dumps(meta, ensure_ascii=False, indent=2))
    on_progress(100.0)
    return DataResult(rows=len(rows), bytes=out.stat().st_size)
