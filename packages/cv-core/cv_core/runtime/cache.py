"""TensorRT engine cache (AD §7.2): engines depend on GPU arch + TRT version, so they are keyed
``{model_sha}-{gpu_arch}-{trt_version}`` and built on a cache miss. Local dir now; R2 later.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Protocol, runtime_checkable

MODEL_SHA_LEN = 16  # hex chars of sha256 kept in the key


def model_sha(path: str | os.PathLike[str]) -> str:
    """sha256 of the ONNX file, truncated to MODEL_SHA_LEN hex chars."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:MODEL_SHA_LEN]


def _token(s: str) -> str:
    """Make a key segment filesystem/object-key safe (no '-' so the key splits cleanly)."""
    return re.sub(r"[^A-Za-z0-9.]+", "_", s.strip()) or "unknown"


def engine_cache_key(model_sha: str, gpu_arch: str, trt_version: str) -> str:
    """``{model_sha}-{gpu_arch}-{trt_version}``, e.g. ``3f2a…-sm89-10.3.0``."""
    if not re.fullmatch(r"[0-9a-f]{8,64}", model_sha):
        raise ValueError(f"model_sha must be lowercase hex: {model_sha!r}")
    return f"{model_sha}-{_token(gpu_arch)}-{_token(trt_version)}"


def detect_gpu_arch(device: int = 0) -> str:
    """Compute capability of an NVIDIA GPU as ``sm{major}{minor}`` (e.g. ``sm89`` for a 4090)."""
    exe = shutil.which("nvidia-smi")
    if exe is None:
        raise RuntimeError("nvidia-smi not found: no NVIDIA GPU to build a TensorRT engine for")
    out = subprocess.run(
        [exe, f"--id={device}", "--query-gpu=compute_cap", "--format=csv,noheader"],
        capture_output=True, text=True, timeout=10, check=True,
    ).stdout.strip()
    major, _, minor = out.partition(".")
    return f"sm{int(major)}{int(minor or 0)}"


def detect_trt_version() -> str:
    try:
        import tensorrt as trt
    except ImportError as e:
        raise RuntimeError("tensorrt is not installed") from e
    return str(trt.__version__)


@runtime_checkable
class EngineCache(Protocol):
    """Pluggable engine store; implementations: local dir (here), R2 (workers, later)."""

    def get(self, key: str) -> bytes | None: ...

    def put(self, key: str, data: bytes) -> None: ...


class LocalDirEngineCache:
    """Engines as ``{root}/{key}.engine``; writes are atomic (tmp file + rename)."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)

    def path(self, key: str) -> Path:
        return self.root / f"{key}.engine"

    def get(self, key: str) -> bytes | None:
        p = self.path(key)
        return p.read_bytes() if p.is_file() else None

    def put(self, key: str, data: bytes) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.root, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, self.path(key))
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


def default_cache_dir() -> Path:
    """``$PITCHVISION_ENGINE_CACHE`` or ``~/.cache/pitchvision/engines``."""
    env = os.environ.get("PITCHVISION_ENGINE_CACHE")
    return Path(env) if env else Path.home() / ".cache" / "pitchvision" / "engines"
