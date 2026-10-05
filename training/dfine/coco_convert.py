"""Detector datasets → one COCO format with the 4 PitchVision classes (M1-CV-04).

Classes (AD §6.5, same ids as tracks.parquet and eval/pveval CLASSES): 0 player, 1 goalkeeper, 2 referee, 3 ball.
Output = COCO instances JSON (``images``, ``annotations`` with ``bbox`` = [x, y, w, h] px, ``categories``) whose
``file_name`` is relative to ``--images-root``. Everything else (staff, coaches, crowd, "other") is dropped; an
image keeps its slot even with zero boxes (true negatives matter for a DETR).

    uv run --project training python training/dfine/coco_convert.py roboflow  SRC_COCO.json  --images DIR --out out.json
    uv run --project training python training/dfine/coco_convert.py soccernet-v3 SN_ROOT --out sn.json  # research-only
    uv run --project training python training/dfine/coco_convert.py cvat     CVAT_COCO.json --images DIR --out ours.json
    uv run --project training python training/dfine/coco_convert.py merge    a.json b.json --out train.json
    uv run --project training python training/dfine/coco_convert.py split    train.json --val 0.1 --by video

Sources (licences: see README.md — nothing here downloads data):
- **Roboflow Universe "football-players-detection"** (COCO export): categories ``ball, goalkeeper, player, referee``
  (+ a supercategory row) — remapped by name.
- **Our CVAT labels** (COCO 1.0 export, eval/README.md): names ``player, goalkeeper, referee, ball`` already.
- **SoccerNet-v3** (``Labels-v3.json`` per game): ``bboxes`` per image with class strings such as
  ``Player team left``, ``Goalkeeper team right``, ``Main referee``, ``Side referee``, ``Ball``, ``Staff members``;
  coordinates ``points.x1/y1/x2/y2`` in pixels of that image. Parser written to the published format — verify on the
  real files before a run (not downloaded here: research-only terms, PLAN G-LEGAL).
- **DFL Bundesliga Data Shootout** (Kaggle): videos + event labels only, *no boxes* — use it for unlabelled frames
  (pseudo-labels from the fine-tuned model, then CVAT review) via ``frames`` → our CVAT path, not as GT directly.
"""
from __future__ import annotations

import argparse
import json
import random
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

CLASSES: tuple[str, ...] = ("player", "goalkeeper", "referee", "ball")
CATEGORIES = [{"id": i, "name": n, "supercategory": "football"} for i, n in enumerate(CLASSES)]

# name (lower-case) → our class; None = drop
NAME_MAP: dict[str, str | None] = {
    "player": "player", "players": "player", "goalkeeper": "goalkeeper", "goalie": "goalkeeper",
    "referee": "referee", "ref": "referee", "ball": "ball", "football": "ball", "sports ball": "ball",
}


def soccernet_class(name: str) -> str | None:
    """SoccerNet-v3 bbox class string → our class (None = drop)."""
    n = name.strip().lower()
    if n == "ball":
        return "ball"
    if n.startswith("goalkeeper"):
        return "goalkeeper"
    if n.startswith("player"):
        return "player"
    if "referee" in n:  # main / side referee
        return "referee"
    return None  # staff members, other, crowd


def new_coco(images: list[dict[str, Any]], anns: list[dict[str, Any]], **info: Any) -> dict[str, Any]:
    return {"info": {"description": "PitchVision detector set", **info}, "categories": CATEGORIES,
            "images": images, "annotations": anns}


def remap_coco(src: dict[str, Any], prefix: str = "", source: str = "") -> dict[str, Any]:
    """Remap a COCO file to our 4 classes by category *name*; drops other categories and degenerate boxes."""
    cat_of = {}
    for c in src.get("categories", []):
        cat_of[c["id"]] = NAME_MAP.get(str(c["name"]).strip().lower())
    images, id_map = [], {}
    for i, im in enumerate(src["images"]):
        id_map[im["id"]] = i
        images.append({"id": i, "file_name": f"{prefix}{im['file_name']}", "width": im["width"],
                       "height": im["height"], **({"source": source} if source else {})})
    anns = []
    for a in src.get("annotations", []):
        cls = cat_of.get(a["category_id"])
        x, y, w, h = a["bbox"]
        if cls is None or w <= 1 or h <= 1 or a["image_id"] not in id_map:
            continue
        anns.append({"id": len(anns), "image_id": id_map[a["image_id"]], "category_id": CLASSES.index(cls),
                     "bbox": [float(x), float(y), float(w), float(h)], "area": float(w * h),
                     "iscrowd": int(a.get("iscrowd", 0))})
    return new_coco(images, anns, source=source)


def soccernet_v3(root: Path) -> dict[str, Any]:
    """All ``Labels-v3.json`` under ``root`` → COCO (``file_name`` = game dir / v3_frames / image name)."""
    images: list[dict[str, Any]] = []
    anns: list[dict[str, Any]] = []
    for lab in sorted(root.rglob("Labels-v3.json")):
        game = lab.parent.relative_to(root)
        data = json.loads(lab.read_text())
        for group in ("actions", "replays"):
            for img_name, item in (data.get(group) or {}).items():
                meta = item.get("imageMetadata", {})
                w, h = int(meta.get("width", 1920)), int(meta.get("height", 1080))
                img_id = len(images)
                images.append({"id": img_id, "file_name": str(game / "v3_frames" / img_name), "width": w,
                               "height": h, "source": "soccernet-v3", "replay": group == "replays"})
                for b in item.get("bboxes", []):
                    cls = soccernet_class(str(b.get("class", "")))
                    p = b.get("points") or {}
                    if cls is None or not {"x1", "y1", "x2", "y2"} <= p.keys():
                        continue
                    x1, y1, x2, y2 = (float(p[k]) for k in ("x1", "y1", "x2", "y2"))
                    x1, x2 = sorted((max(0.0, x1), min(float(w), x2)))
                    y1, y2 = sorted((max(0.0, y1), min(float(h), y2)))
                    if x2 - x1 <= 1 or y2 - y1 <= 1:
                        continue
                    anns.append({"id": len(anns), "image_id": img_id, "category_id": CLASSES.index(cls),
                                 "bbox": [x1, y1, x2 - x1, y2 - y1], "area": (x2 - x1) * (y2 - y1), "iscrowd": 0})
    return new_coco(images, anns, source="soccernet-v3", licence="research-only (G-LEGAL)")


def merge(parts: Iterable[dict[str, Any]]) -> dict[str, Any]:
    images: list[dict[str, Any]] = []
    anns: list[dict[str, Any]] = []
    for part in parts:
        if [c["name"] for c in part["categories"]] != list(CLASSES):
            raise ValueError("merge expects files already converted to the 4 PitchVision classes")
        off = len(images)
        for im in part["images"]:
            images.append({**im, "id": im["id"] + off})
        for a in part["annotations"]:
            anns.append({**a, "id": len(anns), "image_id": a["image_id"] + off})
    return new_coco(images, anns, source="merged")


def _group_key(file_name: str, by: str) -> str:
    """``image``: no grouping; ``dir``: parent directory (SoccerNet game dir, CVAT ``<video>/<frame>.jpg``);
    ``video``: parent dir + file stem without its trailing frame number (Roboflow ``<clip>_<frame>.jpg``)."""
    p = Path(file_name)
    if by == "image":
        return file_name
    if by == "dir":
        return str(p.parent)
    return str(p.parent / re.sub(r"[_-]?\d+$", "", re.sub(r"\.rf\.[0-9a-f]+$", "", p.stem)))


def split(coco: dict[str, Any], val: float, by: str = "video", seed: int = 0) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split by video/game (no frame of a val video in train — near-duplicate frames would leak)."""
    keys = sorted({_group_key(im["file_name"], by) for im in coco["images"]})
    random.Random(seed).shuffle(keys)
    val_keys = set(keys[:max(1, round(len(keys) * val))]) if keys else set()
    out = []
    for want_val in (False, True):
        ims = [im for im in coco["images"] if (_group_key(im["file_name"], by) in val_keys) == want_val]
        ids = {im["id"] for im in ims}
        out.append(new_coco(ims, [a for a in coco["annotations"] if a["image_id"] in ids], split="val" if want_val
                            else "train"))
    return out[0], out[1]


def stats(coco: dict[str, Any]) -> str:
    counts = [0] * len(CLASSES)
    for a in coco["annotations"]:
        counts[a["category_id"]] += 1
    return f"{len(coco['images'])} images, " + ", ".join(f"{n} {c}" for n, c in zip(CLASSES, counts, strict=True))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("roboflow", "cvat"):
        p = sub.add_parser(name)
        p.add_argument("src", type=Path)
        p.add_argument("--prefix", default="", help="prepended to file_name (e.g. 'roboflow/train/')")
        p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("soccernet-v3")
    p.add_argument("root", type=Path)
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("merge")
    p.add_argument("parts", type=Path, nargs="+")
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("split")
    p.add_argument("src", type=Path)
    p.add_argument("--val", type=float, default=0.1)
    p.add_argument("--by", choices=["video", "dir", "image"], default="video")
    a = ap.parse_args()

    if a.cmd in ("roboflow", "cvat"):
        coco = remap_coco(json.loads(a.src.read_text()), a.prefix, source=a.cmd)
    elif a.cmd == "soccernet-v3":
        coco = soccernet_v3(a.root)
    elif a.cmd == "merge":
        coco = merge(json.loads(p.read_text()) for p in a.parts)
    else:
        tr, va = split(json.loads(a.src.read_text()), a.val, a.by)
        for part, suffix in ((tr, "train"), (va, "val")):
            out = a.src.with_name(f"{a.src.stem}_{suffix}.json")
            out.write_text(json.dumps(part))
            print(f"{out}: {stats(part)}")
        return
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(coco))
    print(f"{a.out}: {stats(coco)}")


if __name__ == "__main__":
    main()
