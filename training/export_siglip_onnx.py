"""Export the SigLIP-base vision tower (HF transformers) → ONNX for ``cv_core.teams.SiglipEmbedder`` (S3/S4 teams).

    uv run --project training python training/export_siglip_onnx.py   # export + parity + registry snippet
    uv run --project training python training/export_siglip_onnx.py --skip-export --bench 20

Graph contract (registered in models/registry.yaml as ``siglip-base-p16-224``):

    input   pixel_values   float32 [N, 3, 224, 224]   RGB, resized to 224×224 (bicubic), (x/255 − 0.5) / 0.5
    output  pooler_output  float32 [N, 768]           attention-pooled image embedding (SP-04 used exactly this)

Pre-processing lives in ``cv_core.teams.embed.preprocess`` (cv2, numpy) and is checked here against the HF
``SiglipImageProcessor`` on shirt-like crops. Weights: google/siglip-base-patch16-224, Apache-2.0.
The batch dim is dynamic (calibration embeds ~1 000 crops in batches of 32). The patch Conv gets explicit pads so
the CoreML EP (MLProgram) can compile it (M4: 18 ms/crop vs 40 ms on the CPU EP, but ~3 min compile per load).
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
from transformers import AutoImageProcessor, SiglipVisionModel

DEFAULT_REPO = "google/siglip-base-patch16-224"
DEFAULT_NAME = "siglip-base-p16-224"


def default_out_dir() -> Path:
    return Path(os.environ.get("PITCHVISION_MODEL_DIR", Path.home() / ".cache" / "pitchvision" / "models"))


class _Pooled(torch.nn.Module):
    def __init__(self, m: torch.nn.Module) -> None:
        super().__init__()
        self.m = m

    def forward(self, pixel_values: torch.Tensor) -> torch.Tensor:
        return self.m(pixel_values=pixel_values).pooler_output


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def export(repo: str, out: Path, opset: int) -> None:
    model = _Pooled(SiglipVisionModel.from_pretrained(repo).eval()).eval()
    dummy = torch.rand(2, 3, 224, 224) * 2 - 1
    out.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    with torch.no_grad():
        torch.onnx.export(model, (dummy,), str(out), input_names=["pixel_values"], output_names=["pooler_output"],
                          dynamic_axes={"pixel_values": {0: "batch"}, "pooler_output": {0: "batch"}},
                          opset_version=opset, do_constant_folding=True, dynamo=False)
    print(f"exported {out} ({out.stat().st_size / 1e6:.1f} MB) in {time.perf_counter() - t0:.0f}s", file=sys.stderr)
    import onnx
    from onnx import helper

    # the patch-embedding Conv is exported with auto_pad=VALID; CoreML's MLProgram converter needs explicit pads
    m = onnx.load(str(out))
    for node in m.graph.node:
        if node.op_type == "Conv" and any(a.name == "auto_pad" for a in node.attribute):
            keep = [a for a in node.attribute if a.name != "auto_pad"]
            del node.attribute[:]
            node.attribute.extend([*keep, helper.make_attribute("pads", [0, 0, 0, 0])])
    onnx.save(m, str(out))
    onnx.checker.check_model(str(out))


def _crops(n: int) -> list[np.ndarray]:
    """Small BGR 'shirt' crops of varying size (the real input: torso crops of 10–60 px)."""
    rng = np.random.default_rng(0)
    out = []
    for _ in range(n):
        h, w = int(rng.integers(12, 64)), int(rng.integers(8, 40))
        base = rng.integers(0, 255, 3)
        out.append(np.clip(base + rng.normal(0, 25, (h, w, 3)), 0, 255).astype(np.uint8))
    return out


def check_parity(repo: str, onnx_path: Path) -> None:
    import cv2
    from cv_core.runtime import Runtime
    from cv_core.teams import SiglipEmbedder

    crops = _crops(16)
    proc = AutoImageProcessor.from_pretrained(repo)
    model = SiglipVisionModel.from_pretrained(repo).eval()
    with torch.no_grad():
        inp = proc(images=[cv2.cvtColor(c, cv2.COLOR_BGR2RGB) for c in crops], return_tensors="pt")
        ref = model(**inp).pooler_output.numpy()
    m = Runtime("ort_cpu").model(str(onnx_path))
    graph = m.run({"pixel_values": inp["pixel_values"].numpy()})["pooler_output"]
    mine = SiglipEmbedder(m, normalize=False)(crops)

    def cos(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        return np.asarray((a * b).sum(1) / (np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1)))

    print(f"graph parity (same pixel_values): min cos {cos(ref, graph).min():.6f}, "
          f"max |Δ| {np.abs(ref - graph).max():.2e}", file=sys.stderr)
    print(f"end-to-end (cv_core cv2 preprocess vs HF PIL): min cos {cos(ref, mine).min():.4f}, "
          f"mean cos {cos(ref, mine).mean():.4f}", file=sys.stderr)


def bench(onnx_path: Path, iters: int, batch: int) -> None:
    from cv_core.runtime import Provider, Runtime, is_available

    x = np.random.default_rng(0).random((batch, 3, 224, 224), dtype=np.float32)
    for p in (Provider.ort_cpu, Provider.ort_coreml):
        if not is_available(p):
            continue
        try:
            m = Runtime(p).model(str(onnx_path))
            m.run(x)
            t0 = time.perf_counter()
            for _ in range(iters):
                m.run(x)
            dt = (time.perf_counter() - t0) / iters
            print(f"bench {p.value:10} (active {m.provider.value}): {dt * 1000 / batch:.1f} ms/crop "
                  f"(batch {batch})", file=sys.stderr)
        except Exception as e:
            print(f"bench {p.value}: failed: {type(e).__name__}: {e}", file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--name", default=DEFAULT_NAME)
    ap.add_argument("--out-dir", type=Path, default=default_out_dir())
    ap.add_argument("--opset", type=int, default=17)
    ap.add_argument("--skip-export", action="store_true", help="only check / bench an existing file")
    ap.add_argument("--bench", type=int, default=0, help="iterations for the CPU / CoreML speed test")
    ap.add_argument("--bench-batch", type=int, default=32)
    a = ap.parse_args()

    out = a.out_dir / f"{a.name}.onnx"
    if not a.skip_export:
        export(a.repo, out, a.opset)
    digest = sha256(out)
    check_parity(a.repo, out)
    if a.bench:
        bench(out, a.bench, a.bench_batch)
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
