"""Fine-tune D-FINE (HF transformers implementation) on the PitchVision 4-class COCO set (M1-CV-04).

    uv run --project training python training/dfine/train_dfine.py --config training/dfine/config.yaml
    uv run --project training python training/dfine/train_dfine.py --config ... --set train.epochs=1

Why the HF implementation and a plain PyTorch loop (not the official D-FINE repo, not HF ``Trainer``):
- one stack for train → ``save_pretrained`` → ``training/export_dfine_onnx.py --repo <ckpt>`` → cv_core ONNX
  graph, with the exact pre/post-processing cv_core mirrors (no re-implementation drift between repos);
- the HF image processor converts COCO annotations to the model's label format (normalised cxcywh);
- a ~150-line loop makes the D-FINE specifics explicit (backbone LR multiplier, no-decay groups, grad clip 0.1,
  EMA) which ``Trainer`` would hide behind callbacks. The official repo (Peterande/D-FINE, Apache-2.0) has stronger
  augmentation (mosaic, multi-scale, stop-aug epochs); if M1 mAP misses the target, port its ``dfine_hgnetv2_m``
  fine-tune config — README.md lists the switch.

Outputs (``out_dir``): ``last/`` and ``best/`` (``save_pretrained`` + processor, EMA weights), ``log.jsonl``.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import random
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import torch
import yaml
from PIL import Image
from torch.utils.data import DataLoader, Dataset
from transformers import AutoImageProcessor, DFineForObjectDetection

CLASSES = ("player", "goalkeeper", "referee", "ball")


# --- config ----------------------------------------------------------------------------------------------------------
def load_config(path: Path, overrides: list[str]) -> dict[str, Any]:
    cfg: dict[str, Any] = yaml.safe_load(path.read_text())
    for ov in overrides:
        key, _, val = ov.partition("=")
        node = cfg
        *parents, leaf = key.split(".")
        for p in parents:
            node = node.setdefault(p, {})
        node[leaf] = yaml.safe_load(val)
    return cfg


# --- data ------------------------------------------------------------------------------------------------------------
class CocoDetection(Dataset[tuple[Image.Image, dict[str, Any]]]):
    def __init__(self, coco_json: Path, images_root: Path, *, train: bool, aug: dict[str, float],
                 seed: int = 0) -> None:
        coco = json.loads(Path(coco_json).read_text())
        names = [c["name"] for c in sorted(coco["categories"], key=lambda c: c["id"])]
        if tuple(names) != CLASSES:
            raise ValueError(f"{coco_json}: categories {names} != {CLASSES} (convert with coco_convert.py)")
        self.root = Path(images_root)
        self.images = coco["images"]
        self.anns: dict[int, list[dict[str, Any]]] = {im["id"]: [] for im in self.images}
        for a in coco["annotations"]:
            self.anns[a["image_id"]].append(a)
        self.train = train
        self.aug = aug
        self.rng = random.Random(seed)

    def __len__(self) -> int:
        return len(self.images)

    def __getitem__(self, i: int) -> tuple[Image.Image, dict[str, Any]]:
        im = self.images[i]
        img = Image.open(self.root / im["file_name"]).convert("RGB")
        anns = [dict(a) for a in self.anns[im["id"]]]
        if self.train:
            img, anns = self._augment(img, anns)
        target = {"image_id": im["id"], "annotations": [
            {"bbox": a["bbox"], "category_id": a["category_id"], "area": a["bbox"][2] * a["bbox"][3], "iscrowd": 0}
            for a in anns]}
        return img, target

    def _augment(self, img: Image.Image, anns: list[dict[str, Any]]) -> tuple[Image.Image, list[dict[str, Any]]]:
        w, h = img.size
        if self.rng.random() < self.aug.get("zoom_in", 0):
            s = self.rng.uniform(0.5, 1.0)
            cw, ch = int(w * s), int(h * s)
            x0, y0 = self.rng.randint(0, w - cw), self.rng.randint(0, h - ch)
            img = img.crop((x0, y0, x0 + cw, y0 + ch))
            kept = []
            for a in anns:
                x, y, bw, bh = a["bbox"]
                nx1, ny1 = max(x - x0, 0), max(y - y0, 0)
                nx2, ny2 = min(x + bw - x0, cw), min(y + bh - y0, ch)
                if nx2 - nx1 > 2 and ny2 - ny1 > 2 and (nx2 - nx1) * (ny2 - ny1) > 0.4 * bw * bh:
                    kept.append({**a, "bbox": [nx1, ny1, nx2 - nx1, ny2 - ny1]})
            anns, (w, h) = kept, (cw, ch)
        if self.rng.random() < self.aug.get("hflip", 0):
            img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            anns = [{**a, "bbox": [w - a["bbox"][0] - a["bbox"][2], *a["bbox"][1:]]} for a in anns]
        cj = self.aug.get("color_jitter", 0)
        if cj:
            arr = np.asarray(img, dtype=np.float32)
            arr = arr * self.rng.uniform(1 - cj, 1 + cj) + self.rng.uniform(-cj, cj) * 64
            img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
        return img, anns


class Collate:
    def __init__(self, processor: Any) -> None:
        self.processor = processor

    def __call__(self, batch: list[tuple[Image.Image, dict[str, Any]]]) -> dict[str, Any]:
        images, targets = zip(*batch, strict=True)
        enc = self.processor(images=list(images), annotations=list(targets), return_tensors="pt")
        return {"pixel_values": enc["pixel_values"], "labels": enc["labels"]}


# --- training --------------------------------------------------------------------------------------------------------
class Ema:
    def __init__(self, model: torch.nn.Module, decay: float) -> None:
        self.decay = decay
        self.model = copy.deepcopy(model).eval()
        for p in self.model.parameters():
            p.requires_grad_(False)
        self.updates = 0

    @torch.no_grad()
    def update(self, model: torch.nn.Module) -> None:
        self.updates += 1
        d = self.decay * (1 - math.exp(-self.updates / 2000))  # warm-up as in D-FINE / YOLO EMA
        msd = model.state_dict()
        for k, v in self.model.state_dict().items():
            if v.dtype.is_floating_point:
                v.mul_(d).add_(msd[k].detach(), alpha=1 - d)


def param_groups(model: torch.nn.Module, lr: float, backbone_mult: float, wd: float) -> list[dict[str, Any]]:
    groups: dict[tuple[bool, bool], list[torch.nn.Parameter]] = {}
    for name, p in model.named_parameters():
        if not p.requires_grad:
            continue
        backbone = ".backbone." in f".{name}"
        no_decay = p.ndim <= 1 or name.endswith(".bias") or "norm" in name
        groups.setdefault((backbone, no_decay), []).append(p)
    return [{"params": ps, "lr": lr * (backbone_mult if bb else 1.0), "weight_decay": 0.0 if nd else wd}
            for (bb, nd), ps in groups.items()]


def pick_device() -> torch.device:
    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def to_device(batch: dict[str, Any], dev: torch.device) -> dict[str, Any]:
    return {"pixel_values": batch["pixel_values"].to(dev),
            "labels": [{k: v.to(dev) for k, v in t.items()} for t in batch["labels"]]}


@torch.no_grad()
def evaluate(model: torch.nn.Module, loader: DataLoader[Any], dev: torch.device, max_batches: int = 0) -> float:
    model.eval()
    losses = []
    for i, batch in enumerate(loader):
        if max_batches and i >= max_batches:
            break
        out = model(**to_device(batch, dev))
        losses.append(float(out.loss))
    return float(np.mean(losses)) if losses else float("nan")


def train(cfg: dict[str, Any]) -> dict[str, Any]:
    t_cfg, d_cfg = cfg["train"], cfg["data"]
    torch.manual_seed(t_cfg.get("seed", 0))
    dev = torch.device(t_cfg["device"]) if t_cfg.get("device", "auto") != "auto" else pick_device()
    amp = t_cfg.get("amp", "none") if dev.type == "cuda" else "none"
    amp_dtype = {"bf16": torch.bfloat16, "fp16": torch.float16}.get(amp)
    out_dir = Path(cfg["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    processor = AutoImageProcessor.from_pretrained(cfg["base"])
    processor.size = {"height": d_cfg["imgsz"], "width": d_cfg["imgsz"]}
    id2label = dict(enumerate(cfg.get("classes", CLASSES)))
    model = DFineForObjectDetection.from_pretrained(
        cfg["base"], num_labels=len(id2label), id2label=id2label, label2id={v: k for k, v in id2label.items()},
        ignore_mismatched_sizes=True,  # COCO-80 class heads → 4 classes
    ).to(dev)
    aug = {k: float(d_cfg.get(k, 0)) for k in ("hflip", "zoom_in", "color_jitter")}
    root = Path(d_cfg["images_root"])
    ds_tr = CocoDetection(Path(d_cfg["train"]), root, train=True, aug=aug, seed=t_cfg.get("seed", 0))
    ds_va = CocoDetection(Path(d_cfg["val"]), root, train=False, aug={})
    collate = Collate(processor)
    nw = int(t_cfg.get("num_workers", 0)) if dev.type == "cuda" else 0
    dl_tr = DataLoader(ds_tr, batch_size=t_cfg["batch_size"], shuffle=True, collate_fn=collate, num_workers=nw,
                       drop_last=len(ds_tr) > t_cfg["batch_size"], pin_memory=dev.type == "cuda")
    dl_va = DataLoader(ds_va, batch_size=t_cfg["batch_size"], shuffle=False, collate_fn=collate, num_workers=nw)

    opt = torch.optim.AdamW(param_groups(model, t_cfg["lr"], t_cfg["backbone_lr_mult"], t_cfg["weight_decay"]),
                            lr=t_cfg["lr"])
    steps_per_epoch = max(1, len(dl_tr) // t_cfg.get("grad_accum", 1))
    total = t_cfg["max_steps"] or steps_per_epoch * t_cfg["epochs"]
    warm = min(t_cfg.get("warmup_steps", 0), max(total - 1, 0))

    def lr_lambda(step: int) -> float:
        if step < warm:
            return (step + 1) / warm
        return 0.5 * (1 + math.cos(math.pi * (step - warm) / max(1, total - warm)))

    sched = torch.optim.lr_scheduler.LambdaLR(opt, lr_lambda)
    ema = Ema(model, t_cfg["ema_decay"]) if t_cfg.get("ema_decay", 0) else None
    log_f = (out_dir / "log.jsonl").open("a")
    step, best, t0 = 0, math.inf, time.perf_counter()
    history: list[dict[str, Any]] = []
    epoch = 0
    while step < total:
        model.train()
        for i, batch in enumerate(dl_tr):
            with torch.autocast(dev.type, dtype=amp_dtype, enabled=amp_dtype is not None):
                out = model(**to_device(batch, dev))
            loss = out.loss / t_cfg.get("grad_accum", 1)
            if not torch.isfinite(loss):
                raise FloatingPointError(f"non-finite loss at step {step}: {out.loss_dict}")
            loss.backward()
            if (i + 1) % t_cfg.get("grad_accum", 1):
                continue
            torch.nn.utils.clip_grad_norm_(model.parameters(), t_cfg["clip_grad"])
            opt.step()
            opt.zero_grad(set_to_none=True)
            sched.step()
            if ema:
                ema.update(model)
            step += 1
            rec = {"step": step, "epoch": epoch, "loss": round(float(out.loss), 4), "lr": sched.get_last_lr()[0],
                   "s": round(time.perf_counter() - t0, 1)}
            history.append(rec)
            if step % 20 == 0 or step == total or t_cfg["max_steps"]:
                print(json.dumps(rec), file=sys.stderr)
                log_f.write(json.dumps(rec) + "\n")
            if step >= total:
                break
        epoch += 1
        final_model = ema.model if ema else model
        is_eval_epoch = epoch % t_cfg.get("eval_every_epochs", 1) == 0 or step >= total
        if is_eval_epoch:
            val = evaluate(final_model, dl_va, dev, max_batches=2 if t_cfg["max_steps"] else 0)
            log_f.write(json.dumps({"epoch": epoch, "step": step, "val_loss": val}) + "\n")
            print(f"epoch {epoch} step {step}: val_loss {val:.4f}", file=sys.stderr)
            if val < best:
                best = val
                final_model.save_pretrained(out_dir / "best")
                processor.save_pretrained(out_dir / "best")
    final_model = ema.model if ema else model
    final_model.save_pretrained(out_dir / "last")
    processor.save_pretrained(out_dir / "last")
    log_f.close()
    return {"steps": step, "best_val_loss": best, "device": dev.type, "history": history, "out_dir": str(out_dir)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=Path(__file__).with_name("config.yaml"))
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="override, e.g. train.epochs=1")
    a = ap.parse_args()
    res = train(load_config(a.config, a.set))
    print(json.dumps({k: v for k, v in res.items() if k != "history"}, indent=1))


if __name__ == "__main__":
    main()
