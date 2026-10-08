"""Track corrections (M2-BE-01, SCR-22; AD §8.4): UNEDITED ``tracks.parquet`` + the user's corrections → a new
``tracks.parquet`` that tiles / load_db (+ S9 stats) / the manifest run on, exactly as after S5. No GPU.

The orchestrator always re-applies the video's whole live list to the nearest non-edit ancestor's tracks (an
analysis or a recalibration — never an earlier edit, so an undone correction disappears). Operations run in order
and are each IDEMPOTENT:

- ``merge {from, into}``: rows of ``from`` take ``into``'s id, team and class (one player seen twice, e.g. across a
  cut). Where both tracks have a row in the same frame the ``into`` row wins (a track can't be in two places).
- ``split {track, at_ms, new_track}``: rows of ``track`` at or after ``at_ms`` become ``new_track`` (an id switch:
  the tracker followed someone else from there). ``new_track`` is fixed by the API when the correction is made.
- ``remove {track, from_ms?, to_ms?}``: drop the track's rows in ``[from_ms, to_ms)`` (whole track when omitted —
  SCR-22 "Not a player": spectators, ball boys, a bench).

Team / role fixes are not here: they are display-level (``PATCH /results/{v}/players`` → manifest track_overrides).
"""
from __future__ import annotations

import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from cv_core.io.parquet import detections_metadata, read_tracks, write_tracks

log = logging.getLogger(__name__)

TRACKS_NAME = "tracks.parquet"
OPS = ("merge", "split", "remove")


class EditInvalid(Exception):
    code = "VALIDATION_FAILED"
    retryable = False


@dataclass(frozen=True)
class EditOutput:
    paths: dict[str, Path]
    applied: int
    skipped: int                       # operations that changed nothing (already applied, or the track is gone)
    rows_before: int
    rows_after: int
    stats: dict[str, Any] = field(default_factory=dict)


def apply_corrections(cols: dict[str, np.ndarray], corrections: Sequence[Mapping[str, Any]]
                      ) -> tuple[dict[str, np.ndarray], int, int]:  # fmt: skip
    """→ (columns, applied, skipped). ``cols`` is not modified."""
    c = {k: v.copy() for k, v in cols.items()}
    applied = skipped = 0
    for op in corrections:
        kind = op.get("type")
        tid, ts = c["track_id"], c["ts_ms"]
        if kind == "merge":
            src, dst = int(op["from"]), int(op["into"])
            mine = tid == src
            if src == dst or not mine.any():
                skipped += 1
                continue
            into = tid == dst
            if into.any():
                team, cls = c["team"][into][0], c["cls"][into][0]
                clash = mine & np.isin(c["frame_idx"], c["frame_idx"][into])
                keep = ~clash
                c = {k: v[keep] for k, v in c.items()}
                mine = c["track_id"] == src
                c["team"][mine], c["cls"][mine] = team, cls
            c["track_id"][mine] = dst
        elif kind == "split":
            src, new, at = int(op["track"]), int(op["new_track"]), int(op["at_ms"])
            after = (tid == src) & (ts >= at)
            if not after.any() or new == src:
                skipped += 1
                continue
            c["track_id"][after] = new
        elif kind == "remove":
            src = int(op["track"])
            lo = int(op["from_ms"]) if op.get("from_ms") is not None else np.iinfo(np.int64).min
            hi = int(op["to_ms"]) if op.get("to_ms") is not None else np.iinfo(np.int64).max
            gone = (tid == src) & (ts >= lo) & (ts < hi)
            if not gone.any():
                skipped += 1
                continue
            c = {k: v[~gone] for k, v in c.items()}
        else:
            raise EditInvalid(f"unknown correction type {kind!r}")
        applied += 1
    order = np.lexsort((c["track_id"], c["frame_idx"]))
    return {k: v[order] for k, v in c.items()}, applied, skipped


def run_edit_tracks(tracks: Path, out_dir: Path, *, corrections: Sequence[Mapping[str, Any]]) -> EditOutput:
    t0 = time.perf_counter()
    table = read_tracks(tracks)
    cols = {k: table.column(k).to_numpy(zero_copy_only=False) for k in table.column_names}
    new, applied, skipped = apply_corrections(cols, corrections)
    meta = {**(detections_metadata(table).get("meta") or {}), "stage": "edit_tracks",
            "corrections": [str(op.get("id", "")) for op in corrections]}  # fmt: skip
    path = write_tracks(Path(out_dir) / TRACKS_NAME, new, meta)
    n0, n1 = len(cols["frame_idx"]), len(new["frame_idx"])
    log.info("edit_tracks: %d correction(s) applied, %d no-op, rows %d → %d", applied, skipped, n0, n1)
    return EditOutput(paths={"tracks": path}, applied=applied, skipped=skipped, rows_before=n0, rows_after=n1,
                      stats={"elapsed_s": round(time.perf_counter() - t0, 3),
                             "tracks_after": len(np.unique(new["track_id"][new["track_id"] >= 0]))})  # fmt: skip
