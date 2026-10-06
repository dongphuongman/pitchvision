"""Model loading shared by the GPU stages (S3 calibrate, S4 infer): registry name → sha256-checked file → Runtime.

Provider: ``PV_RUNTIME_PROVIDER`` (``tensorrt`` / ``ort_cuda`` / ``ort_coreml`` / ``ort_cpu``) or the automatic
TensorRT → ORT CUDA → ORT CPU order. The SigLIP embedder follows the same provider except on CoreML, where it uses
the CPU EP unless ``PV_EMBEDDER_PROVIDER`` says otherwise (its MLProgram compile takes minutes per process).
"""
from __future__ import annotations

import os

from cv_core.detect import DFineDetector
from cv_core.registry import ModelRegistry, sha256_file
from cv_core.runtime import Provider, Runtime
from cv_core.teams import SIGLIP_MODEL, SiglipEmbedder
from pitchvision_schemas import PipelineConfig

# PipelineConfig.detector → registry entry (v1: COCO D-FINE-M; the fine-tuned 4-class model replaces it, M1-CV-04)
DETECTOR_MODELS: dict[tuple[str, int], str] = {("dfine-m", 640): "dfine-m-obj2coco-640"}


def runtime_from_env(prefer: str | None = None) -> Runtime:
    return Runtime.select(prefer or os.environ.get("PV_RUNTIME_PROVIDER") or None)


def detector_model_name(config: PipelineConfig) -> str:
    key = (config.detector, config.detector_imgsz)
    if key not in DETECTOR_MODELS:
        raise KeyError(f"no registered detector for {key} (have {sorted(DETECTOR_MODELS)})")
    return DETECTOR_MODELS[key]


def load_detector(config: PipelineConfig, *, conf: float | None = None, registry: ModelRegistry | None = None,
                  runtime: Runtime | None = None) -> DFineDetector:
    reg = registry or ModelRegistry.load()
    rt = runtime or runtime_from_env()
    path = reg.resolve(detector_model_name(config))
    return DFineDetector(rt.model(str(path)), imgsz=config.detector_imgsz,
                         conf=config.conf_threshold if conf is None else conf)


def load_embedder(*, registry: ModelRegistry | None = None, runtime: Runtime | None = None,
                  name: str = SIGLIP_MODEL) -> tuple[SiglipEmbedder, str]:
    """→ (embedder, sha256 of the weights) — the sha goes into team_model.json so S4 can check it uses the same."""
    reg = registry or ModelRegistry.load()
    if runtime is None:
        env = os.environ.get("PV_EMBEDDER_PROVIDER")
        rt = runtime_from_env(env) if env else runtime_from_env()
        if not env and rt.provider is Provider.ort_coreml:
            rt = Runtime(Provider.ort_cpu)
    else:
        rt = runtime
    path = reg.resolve(name)
    return SiglipEmbedder(rt.model(str(path)), name=name), sha256_file(path)
