"""InferenceModel — one loaded model on one provider, same interface for every tier."""
from __future__ import annotations

import abc
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from cv_core.runtime.providers import ORT_EP, Provider

Dim = int | str | None  # int = fixed, str = symbolic (e.g. "batch"), None = unknown

_ORT_DTYPES: dict[str, type[np.generic]] = {
    "tensor(float)": np.float32,
    "tensor(float16)": np.float16,
    "tensor(double)": np.float64,
    "tensor(int64)": np.int64,
    "tensor(int32)": np.int32,
    "tensor(int8)": np.int8,
    "tensor(uint8)": np.uint8,
    "tensor(bool)": np.bool_,
}


@dataclass(frozen=True, slots=True)
class TensorInfo:
    name: str
    shape: tuple[Dim, ...]
    dtype: np.dtype[Any]

    @property
    def is_dynamic(self) -> bool:
        return any(not isinstance(d, int) or d < 0 for d in self.shape)

    def concrete_shape(self, batch_size: int = 1, fill: int = 1) -> tuple[int, ...]:
        """Shape with the leading dynamic dim = batch_size and other dynamic dims = fill."""
        out = []
        for i, d in enumerate(self.shape):
            if isinstance(d, int) and d >= 0:
                out.append(d)
            else:
                out.append(batch_size if i == 0 else fill)
        return tuple(out)


class InferenceModel(abc.ABC):
    """``run(batch) -> {output_name: array}``. A bare array feeds the single input."""

    name: str
    provider: Provider
    inputs: tuple[TensorInfo, ...]
    outputs: tuple[TensorInfo, ...]

    @abc.abstractmethod
    def _run(self, feeds: dict[str, np.ndarray]) -> dict[str, np.ndarray]: ...

    def run(self, batch: np.ndarray | Mapping[str, np.ndarray]) -> dict[str, np.ndarray]:
        if isinstance(batch, np.ndarray):
            if len(self.inputs) != 1:
                names = [i.name for i in self.inputs]
                raise ValueError(f"model {self.name!r} has inputs {names}; pass a mapping")
            feeds = {self.inputs[0].name: batch}
        else:
            feeds = dict(batch)
        for info in self.inputs:
            if info.name not in feeds:
                raise KeyError(f"missing input {info.name!r} for model {self.name!r}")
            arr = feeds[info.name]
            if arr.dtype != info.dtype:
                feeds[info.name] = arr.astype(info.dtype, copy=False)
        return self._run(feeds)

    def warmup(self, iterations: int = 2, batch_size: int = 1,
               shapes: Mapping[str, Sequence[int]] | None = None) -> None:
        """Run zeros through the model (kernel selection, memory pools). Dynamic dims need ``shapes``
        unless 1 is a valid size for them."""
        shapes = shapes or {}
        feeds = {
            i.name: np.zeros(tuple(shapes.get(i.name, i.concrete_shape(batch_size))), dtype=i.dtype)
            for i in self.inputs
        }
        for _ in range(iterations):
            self._run(feeds)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(name={self.name!r}, provider={self.provider.value})"


def _ort_dims(shape: Sequence[Any]) -> tuple[Dim, ...]:
    return tuple(d if isinstance(d, int | str) else None for d in shape)


class OrtModel(InferenceModel):
    """ONNX Runtime session on CPU / CUDA / CoreML EP."""

    def __init__(self, path: str | Path, provider: Provider, *, name: str | None = None,
                 intra_op_threads: int | None = None) -> None:
        import onnxruntime as ort

        if provider not in ORT_EP:
            raise ValueError(f"{provider} is not an ONNX Runtime provider")
        self.name = name or Path(path).stem
        self.path = Path(path)
        so = ort.SessionOptions()
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        if intra_op_threads:
            so.intra_op_num_threads = intra_op_threads
        eps: list[Any] = [ORT_EP[provider]]
        if provider is not Provider.ort_cpu:
            eps.append(ORT_EP[Provider.ort_cpu])
        if provider is Provider.ort_coreml:
            # MLProgram: the legacy NeuralNetwork format fails on DETR-style graphs (D-FINE, SP/M1-CV-06);
            # MLProgram runs D-FINE-M@640 (static batch 1) ~3× faster than the CPU EP on an M4.
            eps[0] = (ORT_EP[provider], {"ModelFormat": "MLProgram", "MLComputeUnits": "ALL"})
        self._sess = ort.InferenceSession(str(path), sess_options=so, providers=eps)
        active = self._sess.get_providers()
        # ORT silently drops an EP it cannot initialise; report what actually runs.
        self.provider = provider if ORT_EP[provider] in active else Provider.ort_cpu
        self.inputs = tuple(
            TensorInfo(i.name, _ort_dims(i.shape), np.dtype(_ORT_DTYPES.get(i.type, np.float32)))
            for i in self._sess.get_inputs()
        )
        self.outputs = tuple(
            TensorInfo(o.name, _ort_dims(o.shape), np.dtype(_ORT_DTYPES.get(o.type, np.float32)))
            for o in self._sess.get_outputs()
        )

    def _run(self, feeds: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
        names = [o.name for o in self.outputs]
        return dict(zip(names, self._sess.run(names, feeds), strict=True))
