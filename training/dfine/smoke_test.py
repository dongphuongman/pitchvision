"""Smoke test for the D-FINE fine-tune pipeline on a tiny synthetic COCO set (MPS / CPU, ~1 min).

    uv run --project training python training/dfine/smoke_test.py            # 2 steps, cached D-FINE-M weights

Checks: converters (remap + merge + split) → dataset/augment → HF processor labels → 2 optimiser steps with finite
loss → EMA → save_pretrained → reload + ONNX export of the fine-tuned 4-class head (same exporter as production).
"""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import coco_convert as ds
import train_dfine

BASE = "ustc-community/dfine-medium-obj2coco"
DEVICE = os.environ.get("SMOKE_DEVICE", "cpu")  # MPS backward of the D-FINE decoder fails (torch 2.14): CPU by default


def synth_roboflow(root: Path, n: int = 6) -> Path:
    """Roboflow-style COCO export: categories incl. a supercategory row, ids not in our order."""
    rng = np.random.default_rng(0)
    cats = [{"id": 0, "name": "players", "supercategory": "none"}, {"id": 1, "name": "ball"},
            {"id": 2, "name": "goalkeeper"}, {"id": 3, "name": "player"}, {"id": 4, "name": "referee"}]
    images, anns = [], []
    (root / "imgs").mkdir(parents=True)
    for i in range(n):
        img = Image.new("RGB", (480, 270), (40, 140, 40))
        d = ImageDraw.Draw(img)
        for _ in range(5):
            x, y = int(rng.integers(10, 440)), int(rng.integers(10, 200))
            cat = int(rng.integers(1, 5))
            w, h = (8, 8) if cat == 1 else (14, 40)
            d.rectangle([x, y, x + w, y + h], fill=[(255, 255, 255), (250, 200, 0), (200, 30, 30),
                                                    (20, 20, 20)][cat - 1])
            anns.append({"id": len(anns), "image_id": i, "category_id": cat, "bbox": [x, y, w, h], "iscrowd": 0})
        img.save(root / "imgs" / f"clip{i // 3}_{i:04d}.jpg")
        images.append({"id": i, "file_name": f"clip{i // 3}_{i:04d}.jpg", "width": 480, "height": 270})
    p = root / "roboflow.json"
    p.write_text(json.dumps({"images": images, "annotations": anns, "categories": cats}))
    return p


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        coco = ds.remap_coco(json.loads(synth_roboflow(root).read_text()), prefix="imgs/", source="roboflow")
        assert [c["name"] for c in coco["categories"]] == list(ds.CLASSES)
        merged = ds.merge([coco])
        tr, va = ds.split(merged, 0.5, by="video")
        assert tr["images"] and va["images"]
        (root / "train.json").write_text(json.dumps(tr))
        (root / "val.json").write_text(json.dumps(va))
        print("data:", ds.stats(tr), "|", ds.stats(va))

        cfg = train_dfine.load_config(Path(__file__).with_name("config.yaml"), [
            f"base={BASE}", f"data.train={root / 'train.json'}", f"data.val={root / 'val.json'}",
            f"data.images_root={root}", "data.imgsz=320", "train.batch_size=2", "train.max_steps=2",
            "train.warmup_steps=1", "train.ema_decay=0.99", f"out_dir={root / 'run'}", "data.zoom_in=1.0",
            f"train.device={DEVICE}",
        ])
        res = train_dfine.train(cfg)
        assert res["steps"] == 2 and all(math.isfinite(h["loss"]) for h in res["history"]), res
        assert (root / "run" / "last" / "config.json").is_file()
        print(f"train: {res['steps']} steps on {res['device']}, losses {[h['loss'] for h in res['history']]}, "
              f"val_loss {res['best_val_loss']:.3f}")

        import export_dfine_onnx as ex

        onnx_path = root / "ft.onnx"
        ex.export(str(root / "run" / "last"), onnx_path, 320, 17, dynamic_batch=False)
        from cv_core.detect import DFineDetector
        from cv_core.runtime import Runtime

        det = DFineDetector(Runtime("ort_cpu").model(str(onnx_path)), imgsz=320, conf=0.0)
        out = det([np.zeros((270, 480, 3), np.uint8)])[0]
        assert out.labels.max() < 4, "fine-tuned head must have 4 classes"
        print(f"export: {onnx_path.stat().st_size / 1e6:.1f} MB, {len(out)} raw dets, labels ⊂ 0..3  ✓")
    print("smoke test OK")


if __name__ == "__main__":
    main()
