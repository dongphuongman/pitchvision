"""Model registry (AD §5 ``models/``, §13.3): ``models/registry.yaml`` lists name → file, sha256, source, licence.

Weights are not in git: they live in a local cache dir (``PITCHVISION_MODEL_DIR``, default
``~/.cache/pitchvision/models``; R2 in deployment). ``resolve(name)`` returns the cached file after checking its
sha256, so a worker never runs a model that differs from the one the registry (and the golden eval) vouched for.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

import yaml

PathLike = str | os.PathLike[str]


def default_model_dir() -> Path:
    return Path(os.environ.get("PITCHVISION_MODEL_DIR", Path.home() / ".cache" / "pitchvision" / "models"))


def default_registry_path() -> Path:
    env = os.environ.get("PITCHVISION_MODEL_REGISTRY")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "models" / "registry.yaml"  # repo checkout / editable install


@cache
def _sha256_cached(path: str, size: int, mtime_ns: int) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_file(path: PathLike) -> str:
    st = os.stat(path)
    return _sha256_cached(str(path), st.st_size, st.st_mtime_ns)


class ModelChecksumError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelEntry:
    name: str
    version: str
    file: str
    sha256: str
    kind: str
    source: str = ""
    licence: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class ModelRegistry:
    def __init__(self, entries: dict[str, ModelEntry], model_dir: PathLike | None = None) -> None:
        self.entries = entries
        self.model_dir = Path(model_dir) if model_dir is not None else default_model_dir()

    @classmethod
    def load(cls, path: PathLike | None = None, model_dir: PathLike | None = None) -> ModelRegistry:
        data = yaml.safe_load(Path(path or default_registry_path()).read_text()) or {}
        entries = {}
        for m in data.get("models", []):
            known = {"name", "version", "file", "sha256", "kind", "source", "licence"}
            entries[m["name"]] = ModelEntry(
                name=m["name"], version=str(m.get("version", "")), file=m["file"], sha256=m["sha256"],
                kind=m.get("kind", ""), source=m.get("source", ""), licence=m.get("licence", ""),
                extra={k: v for k, v in m.items() if k not in known})
        return cls(entries, model_dir)

    def __getitem__(self, name: str) -> ModelEntry:
        if name not in self.entries:
            raise KeyError(f"model {name!r} not in registry (have {sorted(self.entries)})")
        return self.entries[name]

    def path(self, name: str) -> Path:
        return self.model_dir / self[name].file

    def resolve(self, name: str, *, verify: bool = True) -> Path:
        """Cached file for ``name``; raises ``FileNotFoundError`` / ``ModelChecksumError``."""
        e, p = self[name], self.path(name)
        if not p.is_file():
            raise FileNotFoundError(f"model {name!r} not in {self.model_dir} (expected {e.file}; see models/README.md)")
        if verify and sha256_file(p) != e.sha256:
            raise ModelChecksumError(f"{p} sha256 does not match registry entry {name!r} {e.version}")
        return p
