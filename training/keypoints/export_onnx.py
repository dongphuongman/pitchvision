"""YOLO-pose pitch keypoints (.pt) → ONNX for ``cv_core.pitch.PitchKeypointModel`` (+ parity / provider checks).

    uv run --project training/keypoints python training/keypoints/export_onnx.py \
        --weights runs/pitch-kp/yolo11m-pose-pv-v1/weights/best.pt --name pitch-kp-yolo11m-pv-v1 \
        --imgsz 640 --check-image /path/to/golden_frame.jpg

Contract (see cv_core/pitch/keypoints.py): input ``images [1,3,H,W]`` RGB/255 letterboxed (grey 114, centred),
output ``output0 [1, 4+nc+3K, A]``, no NMS in the graph. Fixed input + static batch 1 by default: CoreML MLProgram
(ORT CoreML EP) needs static shapes; ``--dynamic-batch`` for TensorRT batching on NVIDIA. opset 17 runs on
onnxruntime ≥ 1.16 (CPU / CUDA / CoreML) and TensorRT 8.6+.

Output goes to ``$PITCHVISION_MODEL_DIR/<name>.onnx`` (default ~/.cache/pitchvision/models) — never into git.
Prints the sha256 / bytes for a ``models/registry.yaml`` entry. Do NOT register models built from AGPL/third-party
weights that are not covered by our Enterprise licence or whose training data is not cleared (README: licences).
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import time
from pathlib import Path

import cv2
import numpy as np


def model_dir() -> Path:
    return Path(os.environ.get("PITCHVISION_MODEL_DIR", Path.home() / ".cache" / "pitchvision" / "models"))


def export(weights: Path, out: Path, imgsz: list[int], opset: int, dynamic_batch: bool) -> Path:
    from ultralytics import YOLO

    m = YOLO(str(weights))
    if m.task != "pose":
        raise SystemExit(f"{weights} is a {m.task} model, expected pose")
    kpt_shape = m.model.yaml.get("kpt_shape")
    print(f"model: {weights.name} kpt_shape={kpt_shape} names={m.names}")
    size = imgsz if len(imgsz) == 2 else imgsz * 2
    f = m.export(format="onnx", imgsz=size, opset=opset, simplify=True, dynamic=dynamic_batch, nms=False,
                 half=False, batch=1)
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(f), out)
    return out


def check(onnx_path: Path, weights: Path, image: Path, provider: str) -> None:
    """cv_core decode vs Ultralytics predict on one image: per-keypoint pixel difference."""
    from cv_core.pitch import PitchKeypointModel
    from cv_core.runtime import Runtime
    from ultralytics import YOLO

    img = cv2.imread(str(image))
    if img is None:
        raise SystemExit(f"cannot read {image}")
    rt = Runtime(provider, models={"kp": onnx_path})
    kp_model = PitchKeypointModel(rt.model("kp"), conf=0.25)
    ours = kp_model([img])[0]
    H, W = kp_model.imgsz
    # Reference = Ultralytics running the SAME onnx graph (exact parity expected). Its .pt predict path letterboxes
    # to the minimal stride-32 rectangle instead (auto=True), which shifts keypoints by a few px — not a bug.
    # Ultralytics clips keypoints to the frame; we keep off-frame coordinates (estimate_homography drops them),
    # so only in-frame keypoints are compared.
    del weights
    ref = YOLO(str(onnx_path), task="pose").predict(img, imgsz=[H, W], conf=0.25, device="cpu", verbose=False)[0]
    if ref.keypoints is None or len(ref.keypoints) == 0:
        print("parity: Ultralytics found no instance; ours score", ours.score)
        return
    rxy = ref.keypoints.xy[0].cpu().numpy()
    rcf = ref.keypoints.conf[0].cpu().numpy()
    h, w = img.shape[:2]
    vis = (rcf > 0.5) & (ours.xy[:, 0] > 0) & (ours.xy[:, 0] < w) & (ours.xy[:, 1] > 0) & (ours.xy[:, 1] < h)
    d = np.linalg.norm(ours.xy[vis] - rxy[vis], axis=1)
    print(f"parity ({rt.provider.value}): {vis.sum()} visible kpts, max |Δxy| = {d.max() if len(d) else 0:.2f} px, "
          f"max |Δconf| = {np.abs(ours.conf - rcf).max():.4f}")
    n = 20
    t0 = time.perf_counter()
    for _ in range(n):
        kp_model([img])
    print(f"latency ({rt.provider.value}): {(time.perf_counter() - t0) / n * 1000:.1f} ms/frame incl. letterbox")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--weights", type=Path, required=True)
    ap.add_argument("--name", required=True, help="registry name; file = <name>.onnx")
    ap.add_argument("--out-dir", type=Path, default=None, help="default $PITCHVISION_MODEL_DIR")
    ap.add_argument("--imgsz", type=int, nargs="+", default=[640], help="H [W], e.g. 640 or 384 640")
    ap.add_argument("--opset", type=int, default=17)
    ap.add_argument("--dynamic-batch", action="store_true")
    ap.add_argument("--check-image", type=Path)
    ap.add_argument("--providers", nargs="+", default=["ort_cpu", "ort_coreml"])
    a = ap.parse_args()
    out = (a.out_dir or model_dir()) / f"{a.name}.onnx"
    export(a.weights, out, a.imgsz, a.opset, a.dynamic_batch)
    data = out.read_bytes()
    print(f"wrote {out}\n  sha256: {hashlib.sha256(data).hexdigest()}\n  bytes: {len(data)}")
    if a.check_image:
        for p in a.providers:
            try:
                check(out, a.weights, a.check_image, p)
            except Exception as e:  # provider missing on this machine
                print(f"check on {p} failed: {e}")


if __name__ == "__main__":
    main()
