"""Launch the YOLOv11-pose pitch keypoint fine-tune (config.yaml; recipe in README.md).

    uv run --project training/keypoints python training/keypoints/train.py --config training/keypoints/config.yaml \
        --set data=training/keypoints/data/kp/yolo/data.yaml --set train.epochs=150

``--set key=value`` overrides (dotted keys into ``train:``; values parsed as YAML). Prints the best checkpoint path
and the export command. Ultralytics is AGPL-3.0 — this env is separate from cv-core on purpose (README: licences).
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import yaml


def load_config(path: Path, overrides: list[str]) -> dict[str, Any]:
    cfg: dict[str, Any] = yaml.safe_load(path.read_text())
    for o in overrides:
        key, _, val = o.partition("=")
        node = cfg
        parts = key.split(".")
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = yaml.safe_load(val)
    return cfg


def resolve_device(dev: Any) -> Any:
    if dev != "auto":
        return dev
    import torch

    return 0 if torch.cuda.is_available() else "cpu"


def train(cfg: dict[str, Any]) -> Path:
    from ultralytics import YOLO

    args = dict(cfg.get("train", {}))
    args["device"] = resolve_device(args.get("device", "auto"))
    if args["device"] == "cpu":
        args["amp"] = False
    model = YOLO(cfg["base"])
    model.train(data=str(cfg["data"]), **args)
    best = Path(model.trainer.best)  # type: ignore[union-attr]
    return best if best.is_file() else Path(model.trainer.last)  # type: ignore[union-attr]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=Path(__file__).with_name("config.yaml"))
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    a = ap.parse_args()
    cfg = load_config(a.config, a.set)
    best = train(cfg)
    print(f"\nbest checkpoint: {best}")
    print("export: uv run --project training/keypoints python training/keypoints/export_onnx.py "
          f"--weights {best} --name pitch-kp-yolo11m-pv-v1 --imgsz {cfg['train'].get('imgsz', 640)}")


if __name__ == "__main__":
    main()
