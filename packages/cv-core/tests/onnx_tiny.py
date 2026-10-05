"""Tiny ONNX model, hand-encoded protobuf (no `onnx` dependency needed; ORT validates it on load)."""
from __future__ import annotations

import numpy as np

# --- minimal protobuf writer for the handful of ONNX messages we need -------------------------


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b, n = n & 0x7F, n >> 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _f_int(field: int, v: int) -> bytes:
    return _varint(field << 3) + _varint(v)


def _f_bytes(field: int, v: bytes | str) -> bytes:
    b = v.encode() if isinstance(v, str) else v
    return _varint(field << 3 | 2) + _varint(len(b)) + b


FLOAT = 1  # TensorProto.DataType.FLOAT


def _value_info(name: str, dims: list[int | str]) -> bytes:
    shape = b"".join(_f_bytes(1, _f_int(1, d) if isinstance(d, int) else _f_bytes(2, d)) for d in dims)
    tensor_type = _f_int(1, FLOAT) + _f_bytes(2, shape)
    return _f_bytes(1, name) + _f_bytes(2, _f_bytes(1, tensor_type))


def _initializer(name: str, arr: np.ndarray) -> bytes:
    arr = np.ascontiguousarray(arr, dtype="<f4")
    return b"".join(_f_int(1, d) for d in arr.shape) + _f_int(2, FLOAT) + _f_bytes(8, name) + _f_bytes(9, arr.tobytes())


def _node(op: str, inputs: list[str], outputs: list[str]) -> bytes:
    return b"".join(_f_bytes(1, i) for i in inputs) + b"".join(_f_bytes(2, o) for o in outputs) + _f_bytes(4, op)


W = np.arange(12, dtype=np.float32).reshape(4, 3) / 10
B = np.array([1.0, -100.0, 0.5], dtype=np.float32)


def tiny_model_bytes() -> bytes:
    """y = x @ W + B ; z = relu(y). Input x: [batch, 4] float32 (dynamic batch)."""
    graph = (
        _f_bytes(1, _node("MatMul", ["x", "W"], ["m"]))
        + _f_bytes(1, _node("Add", ["m", "B"], ["y"]))
        + _f_bytes(1, _node("Relu", ["y"], ["z"]))
        + _f_bytes(2, "tiny")
        + _f_bytes(5, _initializer("W", W))
        + _f_bytes(5, _initializer("B", B))
        + _f_bytes(11, _value_info("x", ["batch", 4]))
        + _f_bytes(12, _value_info("y", ["batch", 3]))
        + _f_bytes(12, _value_info("z", ["batch", 3]))
    )
    opset = _f_bytes(1, "") + _f_int(2, 13)
    return _f_int(1, 8) + _f_bytes(2, "pitchvision-test") + _f_bytes(7, graph) + _f_bytes(8, opset)
