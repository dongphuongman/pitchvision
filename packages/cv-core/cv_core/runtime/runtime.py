"""Runtime — loads models by name/path on the selected provider tier (AD §6.3, §7.2)."""
from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path

from cv_core.runtime.cache import (
    EngineCache,
    LocalDirEngineCache,
    default_cache_dir,
    detect_gpu_arch,
    detect_trt_version,
    engine_cache_key,
    model_sha,
)
from cv_core.runtime.model import InferenceModel, OrtModel
from cv_core.runtime.providers import Provider, select_provider

log = logging.getLogger(__name__)

PathLike = str | os.PathLike[str]


class Runtime:
    """``rt = Runtime.select(); det = rt.model("detector"); det.run(batch)``.

    Models resolve as: explicit ``models`` mapping → existing file path → ``{model_dir}/{name}.onnx``.
    Loaded models are cached per name.
    """

    def __init__(self, provider: Provider | str, *, models: Mapping[str, PathLike] | None = None,
                 model_dir: PathLike | None = None, engine_cache: EngineCache | None = None,
                 trt_max_batch: int = 16) -> None:
        self._provider = Provider(provider)
        self._models = {k: Path(v) for k, v in (models or {}).items()}
        self._model_dir = Path(model_dir) if model_dir is not None else None
        self._engine_cache = engine_cache
        self._trt_max_batch = trt_max_batch
        self._loaded: dict[str, InferenceModel] = {}

    @classmethod
    def select(cls, prefer: Provider | str | None = None, *, strict: bool = False, **kwargs: object) -> Runtime:
        """Auto-pick TensorRT → ORT CUDA → ORT CPU; ``prefer="ort_coreml"`` opts into CoreML."""
        return cls(select_provider(prefer, strict=strict), **kwargs)  # type: ignore[arg-type]

    @property
    def provider(self) -> Provider:
        return self._provider

    @property
    def engine_cache(self) -> EngineCache:
        if self._engine_cache is None:
            self._engine_cache = LocalDirEngineCache(default_cache_dir())
        return self._engine_cache

    def register(self, name: str, path: PathLike) -> None:
        self._models[name] = Path(path)
        self._loaded.pop(name, None)

    def resolve(self, name: str) -> Path:
        if name in self._models:
            return self._models[name]
        p = Path(name)
        if p.is_file():
            return p
        if self._model_dir is not None and (cand := self._model_dir / f"{name}.onnx").is_file():
            return cand
        raise FileNotFoundError(f"model {name!r} not registered and not found as a path or in {self._model_dir}")

    def engine_key(self, name: str) -> str:
        """TensorRT cache key for a model on this machine's GPU (raises without NVIDIA/TRT)."""
        return engine_cache_key(model_sha(self.resolve(name)), detect_gpu_arch(), detect_trt_version())

    def model(self, name: str, *, warmup: bool = False) -> InferenceModel:
        if name not in self._loaded:
            path = self.resolve(name)
            label = Path(name).stem if name not in self._models else name
            m = self._load(path, label)
            if warmup:
                m.warmup()
            self._loaded[name] = m
        return self._loaded[name]

    def _load(self, path: Path, name: str) -> InferenceModel:
        if self._provider is Provider.tensorrt:
            try:
                return self._load_trt(path, name)
            except (RuntimeError, ValueError) as e:
                log.warning("TensorRT load of %s failed (%s); falling back to ONNX Runtime CUDA", name, e)
                return OrtModel(path, Provider.ort_cuda, name=name)
        return OrtModel(path, self._provider, name=name)

    def _load_trt(self, path: Path, name: str) -> InferenceModel:
        from cv_core.runtime.trt import TrtModel, build_engine

        key = self.engine_key(str(path))
        blob = self.engine_cache.get(key)
        if blob is None:
            log.info("TensorRT engine cache miss %s; building (one-off per GPU arch)", key)
            blob = build_engine(path, max_batch=self._trt_max_batch)
            self.engine_cache.put(key, blob)
        return TrtModel(blob, name=name)

    def __repr__(self) -> str:
        return f"Runtime(provider={self._provider.value}, loaded={sorted(self._loaded)})"
