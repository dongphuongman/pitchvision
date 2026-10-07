"""PnLCalib single-view keypoint HRNet → ONNX for cv_core.pitch.HeatmapPitchModel (research-only weights, internal
demo; see models/pitch-kp-pnlcalib-sv.md). Runs in this folder's own venv (GPL code stays here, never in cv-core).

  .venv/bin/python export_onnx.py [--out ~/.cache/pitchvision/models/pitch-kp-pnlcalib-sv-540x960.onnx]

Graph: input ``image [1, 3, 540, 960]`` RGB float /255 (stretched, no normalisation) → output ``heatmaps
[1, 57, 270, 480]`` (the background channel is dropped in-graph). Prints sha256 + bytes for models/registry.yaml.
"""
import argparse
import hashlib
import os
from pathlib import Path

import numpy as np
import torch

import eval_frames as ef


class KeypointsOnly(torch.nn.Module):
    def __init__(self, net: torch.nn.Module) -> None:
        super().__init__()
        self.net = net

    def forward(self, image: torch.Tensor) -> torch.Tensor:
        return self.net(image)[:, :-1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.expanduser("~/.cache/pitchvision/models/pitch-kp-pnlcalib-sv-540x960.onnx"))
    a = ap.parse_args()
    model, _ = ef.load("cpu")
    m = KeypointsOnly(model).eval()
    x = torch.rand(1, 3, 540, 960)
    with torch.no_grad():
        ref = m(x).numpy()
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(m, (x,), str(out), input_names=["image"], output_names=["heatmaps"], opset_version=17,
                      dynamo=False)
    import onnxruntime as ort  # noqa: PLC0415

    got = ort.InferenceSession(str(out), providers=["CPUExecutionProvider"]).run(None, {"image": x.numpy()})[0]
    print("shape", got.shape, "max abs diff vs torch", float(np.abs(got - ref).max()))
    print("sha256", hashlib.sha256(out.read_bytes()).hexdigest(), "bytes", out.stat().st_size, "->", out)


if __name__ == "__main__":
    main()
