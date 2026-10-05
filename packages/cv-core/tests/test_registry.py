"""models/registry.yaml loader + sha256 gate."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from cv_core.registry import ModelChecksumError, ModelRegistry, default_registry_path


def test_repo_registry_parses() -> None:
    reg = ModelRegistry.load(default_registry_path())
    assert {"dfine-m-obj2coco-640", "shots-gbm-det", "shots-gbm-colour"} <= set(reg.entries)
    det = reg["shots-gbm-det"]
    assert det.kind == "shot-classifier" and len(det.sha256) == 64
    assert det.extra["requires"] == ["dfine-m-obj2coco-640"]
    assert reg["dfine-m-obj2coco-640"].extra["input"]["pixel_values"]["shape"] == [1, 3, 640, 640]


def test_resolve_checks_sha(tmp_path: Path) -> None:
    blob = b"weights"
    (tmp_path / "m.bin").write_bytes(blob)
    yml = tmp_path / "r.yaml"
    yml.write_text(f"models:\n  - name: m\n    file: m.bin\n    sha256: {hashlib.sha256(blob).hexdigest()}\n"
                   "  - name: bad\n    file: m.bin\n    sha256: " + "0" * 64 + "\n  - name: gone\n    file: x.bin\n"
                   "    sha256: " + "0" * 64 + "\n")
    reg = ModelRegistry.load(yml, model_dir=tmp_path)
    assert reg.resolve("m") == tmp_path / "m.bin"
    with pytest.raises(ModelChecksumError):
        reg.resolve("bad")
    with pytest.raises(FileNotFoundError):
        reg.resolve("gone")
    with pytest.raises(KeyError):
        reg["nope"]
