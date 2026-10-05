"""M1-CV-01 — Runtime tier selection, CPU inference, TensorRT engine cache key."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from cv_core.runtime import (
    LocalDirEngineCache,
    Provider,
    Runtime,
    TensorRTUnavailable,
    build_engine,
    engine_cache_key,
    is_available,
    model_sha,
    select_provider,
)
from cv_core.runtime import providers as prov
from onnx_tiny import B, W

# --- CPU tier end to end -------------------------------------------------------------------------


def test_cpu_tier_runs_tiny_model(tiny_onnx: Path) -> None:
    rt = Runtime.select(prefer="ort_cpu")
    assert rt.provider is Provider.ort_cpu
    m = rt.model(str(tiny_onnx), warmup=True)
    assert m.provider is Provider.ort_cpu
    assert [i.name for i in m.inputs] == ["x"]
    assert m.inputs[0].shape == ("batch", 4) and m.inputs[0].is_dynamic
    assert [o.name for o in m.outputs] == ["y", "z"]
    x = np.random.default_rng(0).normal(size=(5, 4)).astype(np.float32)
    out = m.run(x)
    np.testing.assert_allclose(out["y"], x @ W + B, rtol=1e-5, atol=1e-5)
    np.testing.assert_allclose(out["z"], np.maximum(x @ W + B, 0), rtol=1e-5, atol=1e-5)


def test_run_casts_dtype_and_accepts_mapping(tiny_onnx: Path) -> None:
    m = Runtime("ort_cpu").model(str(tiny_onnx))
    x64 = np.ones((2, 4), dtype=np.float64)
    out = m.run({"x": x64})
    assert out["y"].dtype == np.float32 and out["y"].shape == (2, 3)
    with pytest.raises(KeyError):
        m.run({"nope": x64})


def test_model_registry_and_caching(tiny_onnx: Path, tmp_path: Path) -> None:
    rt = Runtime("ort_cpu", models={"detector": tiny_onnx})
    assert rt.model("detector") is rt.model("detector")
    assert rt.model("detector").name == "detector"
    rt2 = Runtime("ort_cpu", model_dir=tiny_onnx.parent)
    assert rt2.resolve("tiny") == tiny_onnx
    with pytest.raises(FileNotFoundError):
        rt2.model("missing")


def test_cpu_always_available() -> None:
    assert is_available(Provider.ort_cpu)


# --- tier selection with monkeypatched availability ----------------------------------------------


@pytest.fixture
def fake_hw(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    def set_hw(*, trt: bool, gpu: bool, eps: tuple[str, ...]) -> None:
        monkeypatch.setattr(prov, "tensorrt_importable", lambda: trt)
        monkeypatch.setattr(prov, "cuda_device_present", lambda: gpu)
        monkeypatch.setattr(prov, "ort_providers", lambda: eps)

    return set_hw


CPU = ("CPUExecutionProvider",)
CUDA = ("CUDAExecutionProvider", "CPUExecutionProvider")
MAC = ("CoreMLExecutionProvider", "CPUExecutionProvider")


@pytest.mark.parametrize(
    ("trt", "gpu", "eps", "prefer", "expected"),
    [
        (True, True, CUDA, None, Provider.tensorrt),
        (False, True, CUDA, None, Provider.ort_cuda),
        (True, False, CUDA, None, Provider.ort_cpu),        # tensorrt wheel but no GPU
        (False, False, CUDA, None, Provider.ort_cpu),       # onnxruntime-gpu on a CPU box
        (False, False, MAC, None, Provider.ort_cpu),        # CoreML never chosen automatically
        (False, False, MAC, "ort_coreml", Provider.ort_coreml),
        (False, False, CPU, "ort_coreml", Provider.ort_cpu),  # opt-in but unavailable → fallback
        (True, True, CUDA, "ort_cuda", Provider.ort_cuda),    # prefer skips TensorRT
        (True, True, CUDA, "ort_cpu", Provider.ort_cpu),
        (False, False, CPU, "tensorrt", Provider.ort_cpu),    # fallback down the chain
    ],
)
def test_select_provider(fake_hw, trt, gpu, eps, prefer, expected) -> None:  # type: ignore[no-untyped-def]
    fake_hw(trt=trt, gpu=gpu, eps=eps)
    assert select_provider(prefer) is expected
    assert Runtime.select(prefer=prefer).provider is expected


def test_select_strict_raises(fake_hw) -> None:  # type: ignore[no-untyped-def]
    fake_hw(trt=False, gpu=False, eps=CPU)
    with pytest.raises(RuntimeError):
        select_provider("tensorrt", strict=True)
    with pytest.raises(RuntimeError):
        select_provider("ort_coreml", strict=True)


def test_select_no_provider(fake_hw) -> None:  # type: ignore[no-untyped-def]
    fake_hw(trt=False, gpu=False, eps=())
    with pytest.raises(RuntimeError):
        select_provider()


# --- TensorRT engine cache -----------------------------------------------------------------------


def test_engine_cache_key_format(tiny_onnx: Path) -> None:
    sha = model_sha(tiny_onnx)
    assert len(sha) == 16 and int(sha, 16) >= 0
    assert model_sha(tiny_onnx) == sha  # deterministic
    assert engine_cache_key(sha, "sm89", "10.3.0") == f"{sha}-sm89-10.3.0"
    assert engine_cache_key(sha, "sm 8-9", "10.3.0+cuda12") == f"{sha}-sm_8_9-10.3.0_cuda12"
    with pytest.raises(ValueError):
        engine_cache_key("NOT-HEX", "sm89", "10")


def test_engine_key_changes_with_model(tiny_onnx: Path, tmp_path: Path) -> None:
    other = tmp_path / "other.onnx"
    other.write_bytes(tiny_onnx.read_bytes() + b"\x00")
    assert model_sha(other) != model_sha(tiny_onnx)


def test_local_dir_engine_cache(tmp_path: Path) -> None:
    cache = LocalDirEngineCache(tmp_path / "engines")
    key = engine_cache_key("0123456789abcdef", "sm89", "10.3.0")
    assert cache.get(key) is None
    cache.put(key, b"engine-bytes")
    assert cache.get(key) == b"engine-bytes"
    assert (tmp_path / "engines" / f"{key}.engine").is_file()
    assert not list((tmp_path / "engines").glob("*.tmp"))


@pytest.mark.skipif(is_available(Provider.tensorrt), reason="machine has TensorRT")
def test_trt_build_raises_clearly_without_tensorrt(tiny_onnx: Path) -> None:
    with pytest.raises(TensorRTUnavailable, match="TensorRT"):
        build_engine(tiny_onnx)
    assert issubclass(TensorRTUnavailable, NotImplementedError)


@pytest.mark.skipif(not is_available(Provider.tensorrt), reason="needs TensorRT + NVIDIA GPU")
def test_trt_tier_matches_cpu(tiny_onnx: Path, tmp_path: Path) -> None:  # pragma: no cover - GPU only
    rt = Runtime("tensorrt", engine_cache=LocalDirEngineCache(tmp_path))
    x = np.random.default_rng(1).normal(size=(3, 4)).astype(np.float32)
    out = rt.model(str(tiny_onnx)).run(x)
    np.testing.assert_allclose(out["y"], x @ W + B, rtol=1e-2, atol=1e-2)  # FP16
    assert LocalDirEngineCache(tmp_path).get(rt.engine_key(str(tiny_onnx))) is not None


@pytest.mark.skipif(not is_available(Provider.ort_cuda), reason="needs onnxruntime-gpu + NVIDIA GPU")
def test_ort_cuda_tier(tiny_onnx: Path) -> None:  # pragma: no cover - GPU only
    m = Runtime("ort_cuda").model(str(tiny_onnx))
    assert m.provider is Provider.ort_cuda
    x = np.ones((2, 4), dtype=np.float32)
    np.testing.assert_allclose(m.run(x)["y"], x @ W + B, rtol=1e-4, atol=1e-4)


@pytest.mark.skipif(not is_available(Provider.ort_coreml), reason="CoreML EP not in this onnxruntime build")
def test_coreml_opt_in(tiny_onnx: Path) -> None:
    rt = Runtime.select(prefer="ort_coreml")
    assert rt.provider is Provider.ort_coreml
    x = np.ones((2, 4), dtype=np.float32)
    np.testing.assert_allclose(rt.model(str(tiny_onnx)).run(x)["y"], x @ W + B, rtol=1e-3, atol=1e-3)
