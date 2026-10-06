"""detections.parquet schema round-trip (contract: docs/contracts/pipeline-stages.md)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from cv_core.io.parquet import (
    COLUMNS,
    DETECTIONS_SCHEMA,
    DETECTIONS_VERSION,
    DetectionsSchemaError,
    detections_metadata,
    read_detections,
    write_detections,
)

CONTRACT = {
    "frame_idx": pa.int32(), "ts_ms": pa.int32(), "track_id": pa.int32(), "cls": pa.int8(), "team": pa.int8(),
    "x1": pa.int16(), "y1": pa.int16(), "x2": pa.int16(), "y2": pa.int16(), "conf": pa.float16(),
    "on_pitch": pa.bool_(), "px": pa.float32(), "py": pa.float32(),
}


def _cols(n: int = 5) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(0)
    return {
        "frame_idx": np.arange(n), "ts_ms": np.arange(n) * 40, "track_id": rng.integers(1, 9, n),
        "cls": np.r_[np.zeros(n - 1), 3], "team": rng.integers(-1, 2, n),
        "x1": rng.random(n) * 1000, "y1": rng.random(n) * 700, "x2": rng.random(n) * 1000 + 20,
        "y2": rng.random(n) * 700 + 40, "conf": rng.random(n),
    }


def test_schema_matches_contract() -> None:
    assert dict(zip(DETECTIONS_SCHEMA.names, DETECTIONS_SCHEMA.types, strict=True)) == CONTRACT
    assert tuple(CONTRACT) == COLUMNS


def test_round_trip(tmp_path: Path) -> None:
    cols = _cols()
    meta = {"segment": {"index": 3, "t0_ms": 0, "t1_ms": 200}, "fps": 25.0}
    p = write_detections(tmp_path / "seg" / "detections.parquet", cols, meta)
    t = read_detections(p)
    assert t.num_rows == 5 and t.schema.equals(DETECTIONS_SCHEMA, check_metadata=False)
    assert t.column("frame_idx").to_pylist() == [0, 1, 2, 3, 4]
    assert t.column("x1").to_pylist() == np.rint(cols["x1"]).astype(int).tolist()
    np.testing.assert_allclose(t.column("conf").to_numpy().astype(np.float32), cols["conf"], atol=1e-3)
    assert all(np.isnan(t.column("px").to_numpy())) and all(t.column("on_pitch").to_pylist())
    md = detections_metadata(p)
    assert md["schema"] == "detections" and md["version"] == DETECTIONS_VERSION and md["meta"] == meta
    assert pq.ParquetFile(p).metadata.row_group(0).column(0).compression == "ZSTD"
    assert not list(tmp_path.glob("seg/*.tmp"))


def test_empty_segment(tmp_path: Path) -> None:
    p = write_detections(tmp_path / "d.parquet", {k: np.zeros(0) for k in _cols()})
    assert read_detections(p).num_rows == 0


def test_int16_clipping_and_bad_columns(tmp_path: Path) -> None:
    cols = _cols(2)
    cols["x2"] = np.array([40000.0, -40000.0])
    t = read_detections(write_detections(tmp_path / "d.parquet", cols))
    assert t.column("x2").to_pylist() == [32767, -32768]
    with pytest.raises(DetectionsSchemaError):
        write_detections(tmp_path / "x.parquet", {**cols, "bogus": np.zeros(2)})
    with pytest.raises(DetectionsSchemaError):
        write_detections(tmp_path / "x.parquet", {**cols, "ts_ms": np.zeros(3)})


def test_reader_rejects_foreign_files(tmp_path: Path) -> None:
    p = tmp_path / "other.parquet"
    pq.write_table(pa.table({"a": [1]}), p)
    with pytest.raises(DetectionsSchemaError):
        read_detections(p)
