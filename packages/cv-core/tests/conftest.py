"""Shared fixtures for cv-core tests."""
from __future__ import annotations

from pathlib import Path

import pytest
from onnx_tiny import tiny_model_bytes


@pytest.fixture
def tiny_onnx(tmp_path: Path) -> Path:
    p = tmp_path / "tiny.onnx"
    p.write_bytes(tiny_model_bytes())
    return p
