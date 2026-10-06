"""Pipeline parquet files — schemas, writers, readers (contract: docs/contracts/pipeline-stages.md).

- ``detections.parquet`` (S4 v1, one file per segment), described below;
- ``tracks.parquet`` (S5, one per job, AD §6.5): detections columns with GLOBAL track ids, without ``on_pitch``,
  plus ``h_quality float16`` (NaN = no homography for the frame) and ``interpolated bool``;
- ``homography.parquet`` (pitch stage, one per segment): ``frame_idx`` + image→pitch ``h0..h8`` (row-major, NaN =
  none) + smoothed ``h_quality``.

``detections.parquet`` columns:
``frame_idx int32`` (normalized 25 fps timeline) · ``ts_ms int32`` · ``track_id int32`` (segment-local
ByteTrack id ≥ 1; ``-1`` = untracked, used for the ball) · ``cls int8`` (0 player · 1 goalkeeper · 2 referee · 3 ball)
· ``team int8`` (0 / 1, -1 none) · ``x1, y1, x2, y2 int16`` (pixels on the normalized video) · ``conf float16`` ·
``on_pitch bool`` · ``px, py float32`` (pitch metres; NaN until the W4 homography). ZSTD-compressed.

File metadata: ``pitchvision.schema = "detections"``, ``pitchvision.version`` (bump on any column change; readers
refuse unknown major versions) and free-form ``pitchvision.meta`` JSON (segment, fps, stride, models, stats).
"""
from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

DETECTIONS_SCHEMA_NAME = "detections"
DETECTIONS_VERSION = "1.0"

CLS_PLAYER, CLS_GOALKEEPER, CLS_REFEREE, CLS_BALL = 0, 1, 2, 3
NO_TRACK = -1

DETECTIONS_SCHEMA = pa.schema([
    pa.field("frame_idx", pa.int32(), nullable=False),
    pa.field("ts_ms", pa.int32(), nullable=False),
    pa.field("track_id", pa.int32(), nullable=False),
    pa.field("cls", pa.int8(), nullable=False),
    pa.field("team", pa.int8(), nullable=False),
    pa.field("x1", pa.int16(), nullable=False),
    pa.field("y1", pa.int16(), nullable=False),
    pa.field("x2", pa.int16(), nullable=False),
    pa.field("y2", pa.int16(), nullable=False),
    pa.field("conf", pa.float16(), nullable=False),
    pa.field("on_pitch", pa.bool_(), nullable=False),
    pa.field("px", pa.float32(), nullable=False),
    pa.field("py", pa.float32(), nullable=False),
])

_NP_TYPES: dict[str, type[np.generic]] = {
    "frame_idx": np.int32, "ts_ms": np.int32, "track_id": np.int32, "cls": np.int8, "team": np.int8,
    "x1": np.int16, "y1": np.int16, "x2": np.int16, "y2": np.int16, "conf": np.float16, "on_pitch": np.bool_,
    "px": np.float32, "py": np.float32,
}
COLUMNS: tuple[str, ...] = tuple(DETECTIONS_SCHEMA.names)

_KEY_SCHEMA, _KEY_VERSION, _KEY_META = b"pitchvision.schema", b"pitchvision.version", b"pitchvision.meta"


class DetectionsSchemaError(ValueError):
    pass


def _cast(name: str, values: Any) -> np.ndarray:
    a = np.asarray(values)
    dt = _NP_TYPES[name]
    if np.issubdtype(dt, np.integer) and a.size and np.issubdtype(a.dtype, np.floating):
        a = np.rint(a)
    if np.issubdtype(dt, np.integer) and a.size:
        info = np.iinfo(dt)  # type: ignore[type-var]
        a = np.clip(a, info.min, info.max)
    return a.astype(dt, copy=False)


def detections_table(columns: Mapping[str, Any], meta: Mapping[str, Any] | None = None) -> pa.Table:
    """Table from column arrays (missing ``px``/``py`` → NaN, ``on_pitch`` → True); values are cast & clipped."""
    n = len(np.asarray(columns["frame_idx"]))
    cols = dict(columns)
    cols.setdefault("px", np.full(n, np.nan, np.float32))
    cols.setdefault("py", np.full(n, np.nan, np.float32))
    cols.setdefault("on_pitch", np.ones(n, bool))
    unknown = set(cols) - set(COLUMNS)
    missing = set(COLUMNS) - set(cols)
    if unknown or missing:
        raise DetectionsSchemaError(f"columns: missing {sorted(missing)}, unknown {sorted(unknown)}")
    arrays = []
    for name in COLUMNS:
        a = _cast(name, cols[name])
        if len(a) != n:
            raise DetectionsSchemaError(f"column {name} has {len(a)} rows, expected {n}")
        arrays.append(pa.array(a, type=DETECTIONS_SCHEMA.field(name).type))
    md = {_KEY_SCHEMA: DETECTIONS_SCHEMA_NAME.encode(), _KEY_VERSION: DETECTIONS_VERSION.encode(),
          _KEY_META: json.dumps(dict(meta or {}), separators=(",", ":"), default=str).encode()}
    return pa.Table.from_arrays(arrays, schema=DETECTIONS_SCHEMA.with_metadata(md))


def write_detections(path: str | os.PathLike[str], columns: Mapping[str, Any] | pa.Table,
                     meta: Mapping[str, Any] | None = None, *, compression_level: int = 3) -> Path:
    """Write ``detections.parquet`` (ZSTD) atomically (tmp file + rename): a crashed task never leaves half a file."""
    table = columns if isinstance(columns, pa.Table) else detections_table(columns, meta)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    pq.write_table(table, tmp, compression="zstd", compression_level=compression_level)
    os.replace(tmp, p)
    return p


def detections_metadata(path_or_table: str | os.PathLike[str] | pa.Table) -> dict[str, Any]:
    """``{"schema", "version", "meta"}`` from the file footer (cheap: no row groups are read)."""
    schema = path_or_table.schema if isinstance(path_or_table, pa.Table) else pq.read_schema(path_or_table)
    md = schema.metadata or {}
    return {"schema": md.get(_KEY_SCHEMA, b"").decode(), "version": md.get(_KEY_VERSION, b"").decode(),
            "meta": json.loads(md.get(_KEY_META, b"{}") or b"{}")}


def read_detections(path: str | os.PathLike[str]) -> pa.Table:
    """Read and validate a ``detections.parquet`` (schema name, major version, column types)."""
    table = pq.read_table(path)
    md = detections_metadata(table)
    if md["schema"] != DETECTIONS_SCHEMA_NAME:
        raise DetectionsSchemaError(f"{path}: not a detections file (schema={md['schema']!r})")
    if md["version"].split(".")[0] != DETECTIONS_VERSION.split(".")[0]:
        raise DetectionsSchemaError(f"{path}: version {md['version']} not readable by {DETECTIONS_VERSION}")
    if not table.schema.equals(DETECTIONS_SCHEMA, check_metadata=False):
        raise DetectionsSchemaError(f"{path}: columns differ from detections v{DETECTIONS_VERSION}")
    return table


# ---------------------------------------------------------------- generic helpers (tracks / homography files)
def _table(schema: pa.Schema, types: Mapping[str, type[np.generic]], name: str, version: str,
           columns: Mapping[str, Any], meta: Mapping[str, Any] | None) -> pa.Table:
    names = tuple(schema.names)
    unknown, missing = set(columns) - set(names), set(names) - set(columns)
    if unknown or missing:
        raise DetectionsSchemaError(f"{name} columns: missing {sorted(missing)}, unknown {sorted(unknown)}")
    n = len(np.asarray(columns[names[0]]))
    arrays = []
    for col in names:
        a = np.asarray(columns[col])
        dt = types[col]
        if np.issubdtype(dt, np.integer) and a.size and np.issubdtype(a.dtype, np.floating):
            a = np.rint(a)
        if np.issubdtype(dt, np.integer) and a.size:
            info = np.iinfo(dt)  # type: ignore[type-var]
            a = np.clip(a, info.min, info.max)
        a = a.astype(dt, copy=False)
        if len(a) != n:
            raise DetectionsSchemaError(f"{name} column {col} has {len(a)} rows, expected {n}")
        arrays.append(pa.array(a, type=schema.field(col).type))
    md = {_KEY_SCHEMA: name.encode(), _KEY_VERSION: version.encode(),
          _KEY_META: json.dumps(dict(meta or {}), separators=(",", ":"), default=str).encode()}
    return pa.Table.from_arrays(arrays, schema=schema.with_metadata(md))


def _write(table: pa.Table, path: str | os.PathLike[str], compression_level: int = 3) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    pq.write_table(table, tmp, compression="zstd", compression_level=compression_level)
    os.replace(tmp, p)
    return p


def _read(path: str | os.PathLike[str], schema: pa.Schema, name: str, version: str) -> pa.Table:
    table = pq.read_table(path)
    md = detections_metadata(table)
    if md["schema"] != name:
        raise DetectionsSchemaError(f"{path}: not a {name} file (schema={md['schema']!r})")
    if md["version"].split(".")[0] != version.split(".")[0]:
        raise DetectionsSchemaError(f"{path}: version {md['version']} not readable by {version}")
    if not table.schema.equals(schema, check_metadata=False):
        raise DetectionsSchemaError(f"{path}: columns differ from {name} v{version}")
    return table


# ---------------------------------------------------------------- tracks.parquet (S5, AD §6.5)
TRACKS_SCHEMA_NAME = "tracks"
TRACKS_VERSION = "1.0"
TRACKS_SCHEMA = pa.schema([
    *(DETECTIONS_SCHEMA.field(c) for c in ("frame_idx", "ts_ms", "track_id", "cls", "team", "x1", "y1", "x2", "y2",
                                           "conf", "px", "py")),
    pa.field("h_quality", pa.float16(), nullable=False),
    pa.field("interpolated", pa.bool_(), nullable=False),
])
_TRACK_TYPES: dict[str, type[np.generic]] = {
    **{k: v for k, v in _NP_TYPES.items() if k != "on_pitch"}, "h_quality": np.float16, "interpolated": np.bool_}
TRACK_COLUMNS: tuple[str, ...] = tuple(TRACKS_SCHEMA.names)


def tracks_table(columns: Mapping[str, Any], meta: Mapping[str, Any] | None = None) -> pa.Table:
    """``tracks.parquet`` table from column arrays (cast & clipped like detections)."""
    return _table(TRACKS_SCHEMA, _TRACK_TYPES, TRACKS_SCHEMA_NAME, TRACKS_VERSION, columns, meta)


def write_tracks(path: str | os.PathLike[str], columns: Mapping[str, Any] | pa.Table,
                 meta: Mapping[str, Any] | None = None) -> Path:
    """Write ``tracks.parquet`` (ZSTD) atomically."""
    table = columns if isinstance(columns, pa.Table) else tracks_table(columns, meta)
    return _write(table, path)


def read_tracks(path: str | os.PathLike[str]) -> pa.Table:
    return _read(path, TRACKS_SCHEMA, TRACKS_SCHEMA_NAME, TRACKS_VERSION)


# ---------------------------------------------------------------- homography.parquet (pitch stage, per segment)
HOMOGRAPHY_SCHEMA_NAME = "homography"
HOMOGRAPHY_VERSION = "1.0"
H_COLUMNS: tuple[str, ...] = tuple(f"h{i}" for i in range(9))
HOMOGRAPHY_SCHEMA = pa.schema([
    pa.field("frame_idx", pa.int32(), nullable=False),
    *(pa.field(c, pa.float64(), nullable=False) for c in H_COLUMNS),
    pa.field("h_quality", pa.float32(), nullable=False),
])
_H_TYPES: dict[str, type[np.generic]] = {"frame_idx": np.int32, "h_quality": np.float32,
                                         **dict.fromkeys(H_COLUMNS, np.float64)}


def write_homography(path: str | os.PathLike[str], frame_idx: Any, H: Any, quality: Any,
                     meta: Mapping[str, Any] | None = None) -> Path:
    """Per-frame image→pitch homography ``H [N, 3, 3]`` (NaN = none) + smoothed ``h_quality [N]``."""
    Hs = np.asarray(H, np.float64).reshape(-1, 9)
    cols: dict[str, Any] = {"frame_idx": frame_idx, "h_quality": quality}
    cols.update({c: Hs[:, i] for i, c in enumerate(H_COLUMNS)})
    return _write(_table(HOMOGRAPHY_SCHEMA, _H_TYPES, HOMOGRAPHY_SCHEMA_NAME, HOMOGRAPHY_VERSION, cols, meta), path)


def read_homography(path: str | os.PathLike[str]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """→ ``(frame_idx [N] int64, H [N, 3, 3] image→pitch (NaN = none), h_quality [N] float32)``."""
    t = _read(path, HOMOGRAPHY_SCHEMA, HOMOGRAPHY_SCHEMA_NAME, HOMOGRAPHY_VERSION)
    H = np.stack([t.column(c).to_numpy() for c in H_COLUMNS], axis=1).reshape(-1, 3, 3)
    return (t.column("frame_idx").to_numpy().astype(np.int64), H,
            t.column("h_quality").to_numpy().astype(np.float32))
