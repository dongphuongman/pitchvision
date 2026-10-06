"""S7 tiles (AD §8.3/§11.1, M1-BE-13): ``tracks.parquet`` + ``ShotTimeline`` → one gzip JSON tile per 10 s.

Output files ``out_dir/{tile_no:06d}.json.gz`` (the task layer uploads them to ``keys.tile_key(org, video, job,
revision, n)``), one per ``TILE_MS`` window over the WHOLE video duration — tile ``n`` covers ``[n·10 s,
min((n+1)·10 s, duration))``. Every frame of the window is present (``f`` on the normalized ``target_fps`` timeline):
frames of shots that were not analysed (close-ups, replays, …) have ``objs = []`` and ``hq = null``; ``shots`` lists
the ``[f0, f1_exclusive, type]`` runs of the window so the player can grey them out.

Object rows follow ``pitchvision_schemas.tiles``: ``[track_id, cls, team, x1, y1, x2, y2, conf, px, py]`` (px/py null
without homography). Every tile is validated against ``pitchvision_schemas.Tile`` before it is written. Output is
deterministic (sorted rows, fixed rounding, compact JSON, gzip mtime 0): a re-run produces byte-identical objects,
which matters because tiles are cached as immutable.
"""
from __future__ import annotations

import gzip
import json
import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from cv_core.io.parquet import read_tracks
from pitchvision_schemas import TILE_MS, PipelineConfig, ShotTimeline, ShotType, Tile

log = logging.getLogger(__name__)

GZIP_LEVEL = 6


@dataclass(frozen=True)
class TilesOutput:
    paths: dict[int, Path]       # tile_no → out_dir/{tile_no:06d}.json.gz
    tile_count: int
    frames: int
    objects: int
    bytes: int
    stats: dict[str, Any] = field(default_factory=dict)


def tile_count_for(duration_ms: int) -> int:
    return max(1, math.ceil(duration_ms / TILE_MS))


def tile_name(tile_no: int) -> str:
    return f"{tile_no:06d}.json.gz"


def _frame_of_ms(ms: int, fps: int) -> int:
    """First frame index whose timestamp (f·1000/fps) is ≥ ms."""
    return -(-ms * fps // 1000)


def shot_types(timeline: ShotTimeline, ts_ms: np.ndarray) -> list[str]:
    """Shot type of each timestamp (``other`` outside the timeline)."""
    shots = timeline.shots
    if not shots:
        return [ShotType.other.value] * len(ts_ms)
    t1 = np.array([s.t1_ms for s in shots])
    i = np.searchsorted(t1, ts_ms, side="right")
    out = []
    for ts, k in zip(ts_ms.tolist(), i.tolist(), strict=True):
        out.append(shots[k].type.value if k < len(shots) and shots[k].t0_ms <= ts else ShotType.other.value)
    return out


def _runs(types: list[str], f0: int) -> list[tuple[int, int, str]]:
    runs: list[tuple[int, int, str]] = []
    for k, t in enumerate(types):
        if runs and runs[-1][2] == t:
            runs[-1] = (runs[-1][0], f0 + k + 1, t)
        else:
            runs.append((f0 + k, f0 + k + 1, t))
    return runs


def _round_or_none(v: float, nd: int) -> float | None:
    return round(v, nd) if math.isfinite(v) else None


def encode_tile(tile: Tile) -> bytes:
    body = json.dumps(tile.model_dump(mode="json"), separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return gzip.compress(body.encode(), compresslevel=GZIP_LEVEL, mtime=0)


def decode_tile(data: bytes) -> Tile:
    return Tile.model_validate_json(gzip.decompress(data))


def run_tiles(tracks: Path, timeline: ShotTimeline, out_dir: Path, config: PipelineConfig | None = None, *,
              duration_ms: int, frame_count: int | None = None) -> TilesOutput:
    cfg = config or PipelineConfig()
    t_all = time.perf_counter()
    fps = int(cfg.target_fps)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    n_tiles = tile_count_for(duration_ms)
    total_frames = min(frame_count or _frame_of_ms(duration_ms, fps), _frame_of_ms(n_tiles * TILE_MS, fps))

    t = read_tracks(tracks)
    col = {c: t.column(c).to_numpy(zero_copy_only=False) for c in t.column_names}
    order = np.lexsort((col["track_id"], col["frame_idx"]))
    frame = col["frame_idx"][order].astype(np.int64)
    ids, cls, team = (col[c][order].astype(np.int64).tolist() for c in ("track_id", "cls", "team"))
    boxes = [col[c][order].astype(np.int64).tolist() for c in ("x1", "y1", "x2", "y2")]
    conf = col["conf"][order].astype(np.float64).tolist()
    px = col["px"][order].astype(np.float64).tolist()
    py = col["py"][order].astype(np.float64).tolist()
    hq = col["h_quality"][order].astype(np.float64)

    paths: dict[int, Path] = {}
    n_objects = n_bytes = 0
    for n in range(n_tiles):
        t0_ms, t1_ms = n * TILE_MS, min((n + 1) * TILE_MS, duration_ms)
        f0, f1 = _frame_of_ms(t0_ms, fps), min(_frame_of_ms((n + 1) * TILE_MS, fps), total_frames)
        f1 = max(f0, f1)
        fr = np.arange(f0, f1, dtype=np.int64)
        types = shot_types(timeline, (fr * 1000) // fps)
        bounds = np.searchsorted(frame, np.arange(f0, f1 + 1)).tolist()
        frames: list[dict[str, Any]] = []
        for k, f in enumerate(fr.tolist()):
            r0, r1 = bounds[k], bounds[k + 1]
            q = float(hq[r0]) if r1 > r0 else math.nan
            objs = [[ids[i], cls[i], team[i], boxes[0][i], boxes[1][i], boxes[2][i], boxes[3][i], round(conf[i], 3),
                     _round_or_none(px[i], 2), _round_or_none(py[i], 2)] for i in range(r0, r1)]  # fmt: skip
            n_objects += len(objs)
            frames.append({"f": f, "hq": _round_or_none(q, 3), "objs": objs})
        tile = Tile.model_validate({"tile_no": n, "t0_ms": t0_ms, "t1_ms": t1_ms, "fps": fps,
                                    "shots": [list(r) for r in _runs(types, f0)], "frames": frames})
        data = encode_tile(tile)
        p = out / tile_name(n)
        p.write_bytes(data)
        paths[n] = p
        n_bytes += len(data)
    stats = {"tile_count": n_tiles, "frames": total_frames, "objects": n_objects, "bytes": n_bytes,
             "elapsed_s": round(time.perf_counter() - t_all, 3)}
    log.info("S7: %d tiles, %d frames, %d objects, %.1f MB in %.1fs", n_tiles, total_frames, n_objects,
             n_bytes / 1e6, stats["elapsed_s"])
    return TilesOutput(paths=paths, tile_count=n_tiles, frames=total_frames, objects=n_objects, bytes=n_bytes,
                       stats=stats)
