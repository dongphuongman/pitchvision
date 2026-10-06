"""``detections.parquet`` (S4 v1, one file per segment) — schema, writer, reader (contract: pipeline-stages.md).

Columns: ``frame_idx int32`` (normalized 25 fps timeline) · ``ts_ms int32`` · ``track_id int32`` (segment-local
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
