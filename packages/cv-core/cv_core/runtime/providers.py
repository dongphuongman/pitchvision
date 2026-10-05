"""Provider tiers and availability probes (AD §7.2).

Automatic order: TensorRT FP16 → ONNX Runtime CUDA → ONNX Runtime CPU.
CoreML is opt-in only (``prefer="coreml"``): it is never picked automatically.
"""
from __future__ import annotations

import importlib.util
import logging
import shutil
import subprocess
from enum import StrEnum
from functools import cache

log = logging.getLogger(__name__)


class Provider(StrEnum):
    tensorrt = "tensorrt"
    ort_cuda = "ort_cuda"
    ort_coreml = "ort_coreml"
    ort_cpu = "ort_cpu"


AUTO_ORDER: tuple[Provider, ...] = (Provider.tensorrt, Provider.ort_cuda, Provider.ort_cpu)

ORT_EP: dict[Provider, str] = {
    Provider.ort_cuda: "CUDAExecutionProvider",
    Provider.ort_coreml: "CoreMLExecutionProvider",
    Provider.ort_cpu: "CPUExecutionProvider",
}


@cache
def ort_providers() -> tuple[str, ...]:
    """Execution providers compiled into the installed onnxruntime build."""
    import onnxruntime as ort

    return tuple(ort.get_available_providers())


@cache
def cuda_device_present() -> bool:
    """True if an NVIDIA GPU is visible (nvidia-smi lists at least one device)."""
    exe = shutil.which("nvidia-smi")
    if exe is None:
        return False
    try:
        out = subprocess.run([exe, "-L"], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0 and "GPU" in out.stdout


@cache
def tensorrt_importable() -> bool:
    return importlib.util.find_spec("tensorrt") is not None


def is_available(p: Provider) -> bool:
    """Whether provider tier ``p`` can run on this machine."""
    if p is Provider.tensorrt:
        return tensorrt_importable() and cuda_device_present()
    if p is Provider.ort_cuda:
        return ORT_EP[p] in ort_providers() and cuda_device_present()
    return ORT_EP[p] in ort_providers()


def select_provider(prefer: Provider | str | None = None, *, strict: bool = False) -> Provider:
    """Pick the best available tier.

    ``prefer`` moves the search start down the automatic chain (e.g. ``ort_cuda`` skips TensorRT).
    ``ort_coreml`` is only ever chosen when preferred explicitly. With ``strict`` an unavailable
    preference raises instead of falling back.
    """
    pref = Provider(prefer) if prefer is not None else None
    if pref is Provider.ort_coreml:
        if is_available(pref):
            return pref
        if strict:
            raise RuntimeError("CoreML execution provider is not available in this onnxruntime build")
        log.warning("CoreML EP unavailable; falling back to the automatic chain")
        pref = None
    chain = AUTO_ORDER if pref is None else AUTO_ORDER[AUTO_ORDER.index(pref):]
    for p in chain:
        if is_available(p):
            if pref is not None and p is not pref:
                if strict:
                    raise RuntimeError(f"provider {pref} is not available on this machine")
                log.warning("provider %s unavailable; falling back to %s", pref, p)
            return p
    raise RuntimeError("no inference provider available (onnxruntime CPU EP missing?)")
