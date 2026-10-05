"""TensorRT FP16 tier (NVIDIA only). Untested on the Mac dev box: every entry point raises a clear
error when TensorRT / CUDA is missing, and the Runtime never picks this tier there.

Device memory goes through cuda-python (``cuda.bindings.runtime``), which ships with the CUDA image.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from cv_core.runtime.model import InferenceModel, TensorInfo
from cv_core.runtime.providers import Provider, is_available


class TensorRTUnavailable(NotImplementedError):
    """Raised when a TensorRT engine is requested on a machine without TensorRT + an NVIDIA GPU."""


def _require_trt() -> Any:
    if not is_available(Provider.tensorrt):
        raise TensorRTUnavailable(
            "TensorRT engine build/load needs the `tensorrt` package and an NVIDIA GPU; "
            "use Runtime.select() (falls back to ONNX Runtime) on this machine"
        )
    import tensorrt as trt

    return trt


def _cudart() -> Any:
    try:
        from cuda.bindings import runtime as cudart
    except ImportError:
        try:
            from cuda import cudart
        except ImportError as e:
            raise TensorRTUnavailable("cuda-python is required for the TensorRT tier") from e
    return cudart


def _ck(res: Any) -> Any:
    err, *rest = res if isinstance(res, tuple) else (res,)
    if int(err) != 0:
        raise RuntimeError(f"CUDA error {err}")
    return rest[0] if len(rest) == 1 else tuple(rest)


def build_engine(onnx_path: str | Path, *, fp16: bool = True, max_batch: int = 16, workspace_gb: int = 4) -> bytes:
    """Build a serialized engine from ONNX (~2 min per new GPU arch, AD §7.2).

    Only the leading (batch) dim may be dynamic; the optimisation profile spans 1..max_batch.
    """
    trt = _require_trt()
    logger = trt.Logger(trt.Logger.WARNING)
    builder = trt.Builder(logger)
    network = builder.create_network(0)
    parser = trt.OnnxParser(network, logger)
    if not parser.parse(Path(onnx_path).read_bytes()):
        errs = "; ".join(str(parser.get_error(i)) for i in range(parser.num_errors))
        raise RuntimeError(f"TensorRT could not parse {onnx_path}: {errs}")
    config = builder.create_builder_config()
    config.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE, workspace_gb << 30)
    if fp16:
        config.set_flag(trt.BuilderFlag.FP16)
    profile, dynamic = builder.create_optimization_profile(), False
    for i in range(network.num_inputs):
        t = network.get_input(i)
        shape = tuple(t.shape)
        if any(d < 0 for d in shape[1:]):
            raise ValueError(f"input {t.name} has dynamic non-batch dims {shape}; export with static H/W")
        if shape and shape[0] < 0:
            dynamic = True
            rest = shape[1:]
            profile.set_shape(t.name, (1, *rest), (max_batch, *rest), (max_batch, *rest))
    if dynamic:
        config.add_optimization_profile(profile)
    blob = builder.build_serialized_network(network, config)
    if blob is None:
        raise RuntimeError(f"TensorRT engine build failed for {onnx_path}")
    return bytes(blob)


class TrtModel(InferenceModel):
    """Deserialized TensorRT engine; host numpy in/out, device buffers reused between calls."""

    def __init__(self, engine: bytes, *, name: str) -> None:
        trt = _require_trt()
        self._trt = trt
        self._cudart = _cudart()
        self.name = name
        self.provider = Provider.tensorrt
        self._runtime = trt.Runtime(trt.Logger(trt.Logger.WARNING))
        self._engine = self._runtime.deserialize_cuda_engine(engine)
        if self._engine is None:
            raise RuntimeError(f"could not deserialize TensorRT engine for {name}")
        self._ctx = self._engine.create_execution_context()
        self._stream = _ck(self._cudart.cudaStreamCreate())
        self._buffers: dict[str, tuple[int, int]] = {}  # name -> (device ptr, nbytes)
        ins: list[TensorInfo] = []
        outs: list[TensorInfo] = []
        for i in range(self._engine.num_io_tensors):
            n = self._engine.get_tensor_name(i)
            info = TensorInfo(
                n,
                tuple(int(d) if d >= 0 else None for d in self._engine.get_tensor_shape(n)),
                np.dtype(trt.nptype(self._engine.get_tensor_dtype(n))),
            )
            (ins if self._engine.get_tensor_mode(n) == trt.TensorIOMode.INPUT else outs).append(info)
        self.inputs, self.outputs = tuple(ins), tuple(outs)

    def _device(self, name: str, nbytes: int) -> int:
        ptr, size = self._buffers.get(name, (0, 0))
        if size < nbytes:
            if ptr:
                _ck(self._cudart.cudaFree(ptr))
            ptr, size = int(_ck(self._cudart.cudaMalloc(nbytes))), nbytes
            self._buffers[name] = (ptr, size)
        return ptr

    def _run(self, feeds: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
        cu = self._cudart
        h2d, d2h = cu.cudaMemcpyKind.cudaMemcpyHostToDevice, cu.cudaMemcpyKind.cudaMemcpyDeviceToHost
        for info in self.inputs:
            arr = np.ascontiguousarray(feeds[info.name])
            self._ctx.set_input_shape(info.name, arr.shape)
            ptr = self._device(info.name, arr.nbytes)
            _ck(cu.cudaMemcpyAsync(ptr, arr.ctypes.data, arr.nbytes, h2d, self._stream))
            self._ctx.set_tensor_address(info.name, ptr)
        results: dict[str, np.ndarray] = {}
        for info in self.outputs:
            out = np.empty(tuple(self._ctx.get_tensor_shape(info.name)), dtype=info.dtype)
            self._ctx.set_tensor_address(info.name, self._device(info.name, out.nbytes))
            results[info.name] = out
        if not self._ctx.execute_async_v3(self._stream):
            raise RuntimeError(f"TensorRT execution failed for {self.name}")
        for n, out in results.items():
            _ck(cu.cudaMemcpyAsync(out.ctypes.data, self._buffers[n][0], out.nbytes, d2h, self._stream))
        _ck(cu.cudaStreamSynchronize(self._stream))
        return results

    def __del__(self) -> None:
        cu = getattr(self, "_cudart", None)
        for ptr, _ in getattr(self, "_buffers", {}).values():
            if cu is not None and ptr:
                cu.cudaFree(ptr)
