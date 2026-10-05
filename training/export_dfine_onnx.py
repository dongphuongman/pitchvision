"""Export D-FINE (HF transformers) → ONNX for ``cv_core.detect.DFineDetector``.

    uv run --project training python training/export_dfine_onnx.py                       # D-FINE-M obj2coco
    uv run --project training python training/export_dfine_onnx.py --check-image frame.jpg --bench 20

Graph contract (registered in models/registry.yaml, consumed by cv_core.detect.dfine):

    input   pixel_values  float32 [N, 3, 640, 640]   RGB, stretched to 640×640 (bilinear), /255, no mean/std
    output  logits        float32 [N, 300, C]        per-query class logits (sigmoid / focal-loss head)
    output  pred_boxes    float32 [N, 300, 4]        cx, cy, w, h normalised to 0..1

Post-processing (sigmoid → top-k over queries×classes → cxcywh→xyxy → threshold) is NOT in the graph:
it lives in ``cv_core.detect.dfine.postprocess`` (numpy; mirrors HF ``post_process_object_detection``),
so the graph stays a plain backbone+decoder that TensorRT/CoreML can take.

Writes ``<out-dir>/<name>.onnx`` and prints its sha256 + a registry snippet. Weights are Apache-2.0
(ustc-community/D-FINE); the obj2coco checkpoint was pre-trained on Objects365 (licence check open in G-LEGAL).
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
from pathlib import Path

import numpy as np
import torch
from transformers import AutoImageProcessor, AutoModelForObjectDetection

DEFAULT_REPO = "ustc-community/dfine-medium-obj2coco"
DEFAULT_NAME = "dfine-m-obj2coco-640"


def default_out_dir() -> Path:
    return Path(os.environ.get("PITCHVISION_MODEL_DIR", Path.home() / ".cache" / "pitchvision" / "models"))


class _Raw(torch.nn.Module):
    """HF model → (logits, pred_boxes) only (drops aux / intermediate outputs)."""

    def __init__(self, m: torch.nn.Module) -> None:
        super().__init__()
        self.m = m

    def forward(self, pixel_values: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        out = self.m(pixel_values=pixel_values)
        return out.logits, out.pred_boxes


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def export(repo: str, out: Path, imgsz: int, opset: int, dynamic_batch: bool) -> None:
    model = AutoModelForObjectDetection.from_pretrained(repo).eval()
    wrapped = _Raw(model).eval()
    dummy = torch.rand(1, 3, imgsz, imgsz)
    out.parent.mkdir(parents=True, exist_ok=True)
    axes = {"pixel_values": {0: "batch"}, "logits": {0: "batch"}, "pred_boxes": {0: "batch"}} if dynamic_batch else None
    t0 = time.perf_counter()
    with torch.no_grad():
        try:  # legacy TorchScript exporter: most robust for DETR-style graphs
            torch.onnx.export(wrapped, (dummy,), str(out), input_names=["pixel_values"],
                              output_names=["logits", "pred_boxes"], dynamic_axes=axes, opset_version=opset,
                              do_constant_folding=True, dynamo=False)
        except Exception as e:
            print(f"legacy exporter failed ({type(e).__name__}: {e}); trying dynamo=True", file=sys.stderr)
            dyn = {"pixel_values": {0: torch.export.Dim("batch", min=1, max=64)}} if dynamic_batch else None
            prog = torch.onnx.export(wrapped, (dummy,), input_names=["pixel_values"],
                                     output_names=["logits", "pred_boxes"], dynamic_shapes=dyn,
                                     opset_version=max(opset, 18), dynamo=True)
            prog.save(str(out))
    print(f"exported {out} ({out.stat().st_size / 1e6:.1f} MB) in {time.perf_counter() - t0:.0f}s", file=sys.stderr)
    import onnx

    onnx.checker.check_model(str(out))


def _load_image(path: Path | None, imgsz: int) -> np.ndarray:
    import cv2

    if path is not None:
        img = cv2.imread(str(path))
        if img is None:
            raise SystemExit(f"cannot read {path}")
        return img
    rng = np.random.default_rng(0)
    return rng.integers(0, 255, (720, 1280, 3), dtype=np.uint8)


def check_parity(repo: str, onnx_path: Path, image: Path | None, imgsz: int) -> None:
    """ONNX + cv_core post-process vs HF torch + HF post-process on one image."""
    from cv_core.detect.dfine import DFineDetector
    from cv_core.runtime import Runtime

    bgr = _load_image(image, imgsz)
    proc = AutoImageProcessor.from_pretrained(repo)
    model = AutoModelForObjectDetection.from_pretrained(repo).eval()
    import cv2

    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    with torch.no_grad():
        inp = proc(images=rgb, return_tensors="pt")
        ref = model(**inp)
        r = proc.post_process_object_detection(ref, threshold=0.35, target_sizes=[bgr.shape[:2]])[0]
    m = Runtime("ort_cpu").model(str(onnx_path))
    # 1) graph parity: same pixel_values through torch and ONNX, both through the numpy post-process. Raw per-query
    # tensors are not comparable 1:1 (the encoder's top-k query selection may reorder near-tied queries).
    from cv_core.detect.dfine import postprocess

    raw = m.run({"pixel_values": inp["pixel_values"].numpy()})
    hw = [bgr.shape[:2]]
    a = postprocess(ref.logits.numpy(), ref.pred_boxes.numpy(), hw, 0.35)[0]
    b = postprocess(raw["logits"], raw["pred_boxes"], hw, 0.35)[0]
    if len(a) and len(b):
        iou = _iou(a.boxes, b.boxes)
        ok = iou.max(1) > 0.5
        dscore = np.abs(a.scores[ok] - b.scores[iou.argmax(1)[ok]]).max()
        print(f"graph parity: torch {len(a)} / onnx {len(b)} dets, matched {ok.sum()} (mean IoU "
              f"{iou.max(1)[ok].mean():.4f}, max |Δscore| {dscore:.4f})", file=sys.stderr)
    # 2) end-to-end: cv_core preprocess (cv2) + numpy postprocess vs HF processor (PIL) + HF postprocess
    mine = DFineDetector(m, imgsz=imgsz, conf=0.35)([bgr])[0]
    tb, ts = r["boxes"].numpy(), r["scores"].numpy()
    print(f"end-to-end: torch {len(ts)} boxes ≥0.35, onnx+cv_core {len(mine)} boxes", file=sys.stderr)
    if len(ts) and len(mine):
        iou = _iou(tb, mine.boxes)
        j = iou.argmax(1)
        matched = iou.max(1) > 0.5
        print(f"  matched {matched.sum()}/{len(ts)} at IoU>0.5, mean IoU {iou.max(1)[matched].mean():.3f}, "
              f"max |Δscore| {np.abs(ts[matched] - mine.scores[j[matched]]).max():.4f}", file=sys.stderr)


def _iou(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    x1 = np.maximum(a[:, None, 0], b[None, :, 0])
    y1 = np.maximum(a[:, None, 1], b[None, :, 1])
    x2 = np.minimum(a[:, None, 2], b[None, :, 2])
    y2 = np.minimum(a[:, None, 3], b[None, :, 3])
    inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
    area_a = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    area_b = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    return np.asarray(inter / (area_a[:, None] + area_b[None, :] - inter + 1e-9))


def bench(onnx_path: Path, imgsz: int, iters: int) -> None:
    from cv_core.runtime import Provider, Runtime, is_available

    x = np.random.default_rng(0).random((1, 3, imgsz, imgsz), dtype=np.float32)
    for p in (Provider.ort_cpu, Provider.ort_coreml):
        if not is_available(p):
            continue
        try:
            m = Runtime(p).model(str(onnx_path))
            t0 = time.perf_counter()
            m.run(x)
            first = time.perf_counter() - t0
            t0 = time.perf_counter()
            for _ in range(iters):
                m.run(x)
            dt = (time.perf_counter() - t0) / iters
            print(f"bench {p.value:10} (active {m.provider.value}): first {first:.1f}s, "
                  f"{dt * 1000:.0f} ms/frame = {1 / dt:.1f} fps (batch 1, {imgsz}px)", file=sys.stderr)
        except Exception as e:
            print(f"bench {p.value}: failed: {type(e).__name__}: {e}", file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--name", default=DEFAULT_NAME)
    ap.add_argument("--out-dir", type=Path, default=default_out_dir())
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--opset", type=int, default=17)
    ap.add_argument("--dynamic-batch", action="store_true",
                    help="dynamic batch dim (GPU / TensorRT profiles); default static batch 1 — CoreML EP needs it")
    ap.add_argument("--skip-export", action="store_true", help="only check / bench an existing file")
    ap.add_argument("--check-image", type=Path, help="image for torch vs ONNX parity (default: random noise)")
    ap.add_argument("--bench", type=int, default=0, help="iterations for the CPU / CoreML speed test")
    a = ap.parse_args()

    out = a.out_dir / f"{a.name}.onnx"
    if not a.skip_export:
        export(a.repo, out, a.imgsz, a.opset, dynamic_batch=a.dynamic_batch)
    digest = sha256(out)
    check_parity(a.repo, out, a.check_image, a.imgsz)
    if a.bench:
        bench(out, a.imgsz, a.bench)
    print(f"""
# models/registry.yaml entry
  - name: {a.name}
    file: {out.name}
    sha256: {digest}
    bytes: {out.stat().st_size}
    source: https://huggingface.co/{a.repo}
""")


if __name__ == "__main__":
    main()
