"""Inference runtime (AD §7.2): TensorRT FP16 → ONNX Runtime CUDA → ONNX Runtime CPU, picked automatically."""
from cv_core.runtime.cache import (
    EngineCache,
    LocalDirEngineCache,
    detect_gpu_arch,
    detect_trt_version,
    engine_cache_key,
    model_sha,
)
from cv_core.runtime.model import InferenceModel, OrtModel, TensorInfo
from cv_core.runtime.providers import Provider, is_available, select_provider
from cv_core.runtime.runtime import Runtime
from cv_core.runtime.trt import TensorRTUnavailable, build_engine

__all__ = [
    "EngineCache",
    "InferenceModel",
    "LocalDirEngineCache",
    "OrtModel",
    "Provider",
    "Runtime",
    "TensorInfo",
    "TensorRTUnavailable",
    "build_engine",
    "detect_gpu_arch",
    "detect_trt_version",
    "engine_cache_key",
    "is_available",
    "model_sha",
    "select_provider",
]
