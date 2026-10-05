"""Label / prediction file formats read by the eval scripts (see eval/README.md).

Shots      ``{"format": "pv-shot-samples/v1", "samples": [{"video", "t_ms", "label", "conf"?}]}``
           or ``{"format": "pv-shot-timelines/v1", "timelines": {video: {"shots": [Shot…]}}}``
           (pitchvision_schemas.ShotTimeline; sampled at the GT timestamps).
Detection  COCO instances JSON for GT (CVAT "COCO 1.0" export) + COCO results list for predictions.
           Image ``file_name`` = ``{video_id}/{frame_idx:06d}.jpg`` (or ``{video_id}_{frame_idx:06d}.jpg``).
Teams      ``{"format": "pv-team-labels/v1", "items": [{"video", "frame_idx", "obj", "team"}]}`` for both
           GT and predictions, joined on (video, frame_idx, obj); team 0/1, -1 = not a team player.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pitchvision_schemas import ShotTimeline

from pveval.metrics import Box

SHOT_SAMPLES = "pv-shot-samples/v1"
SHOT_TIMELINES = "pv-shot-timelines/v1"
TEAM_LABELS = "pv-team-labels/v1"
# AD §6.5 tracks.parquet classes
CLASSES = {"player": 0, "goalkeeper": 1, "referee": 2, "ball": 3}


@dataclass(frozen=True, slots=True)
class ShotSample:
    video: str
    t_ms: int
    label: str
    conf: float | None = None


def write_shot_samples(path: Path, samples: list[ShotSample], **meta: Any) -> None:
    rows = [_sample_row(s) for s in samples]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"format": SHOT_SAMPLES, **meta, "samples": rows}, indent=1) + "\n")


def _sample_row(s: ShotSample) -> dict[str, Any]:
    row: dict[str, Any] = {"video": s.video, "t_ms": s.t_ms, "label": s.label}
    if s.conf is not None:
        row["conf"] = round(s.conf, 4)
    return row


def read_shot_samples(path: Path) -> list[ShotSample]:
    data = json.loads(Path(path).read_text())
    if data.get("format") != SHOT_SAMPLES:
        raise ValueError(f"{path}: expected format {SHOT_SAMPLES}")
    return [ShotSample(r["video"], int(r["t_ms"]), r["label"], r.get("conf")) for r in data["samples"]]


def read_shot_predictions(path: Path, at: list[ShotSample]) -> dict[tuple[str, int], str | None]:
    """Predicted label at each GT (video, t_ms), from either shot format."""
    data = json.loads(Path(path).read_text())
    fmt = data.get("format")
    if fmt == SHOT_SAMPLES:
        got = {(s.video, s.t_ms): s.label for s in read_shot_samples(path)}
        return {(s.video, s.t_ms): got.get((s.video, s.t_ms)) for s in at}
    if fmt == SHOT_TIMELINES:
        tls = {v: ShotTimeline.model_validate(t) for v, t in data["timelines"].items()}
        out: dict[tuple[str, int], str | None] = {}
        for s in at:
            shot = tls[s.video].at(s.t_ms) if s.video in tls else None
            out[(s.video, s.t_ms)] = shot.type.value if shot else None
        return out
    raise ValueError(f"{path}: unknown shot format {fmt!r}")


# --- detection (COCO) ----------------------------------------------------------------------------


def video_of(file_name: str) -> str:
    """Match id from an image file name (``vid/000123.jpg`` or ``vid_000123.jpg``; ids may contain '_')."""
    p = Path(file_name)
    if len(p.parts) > 1:
        return p.parts[-2]
    return p.stem.rsplit("_", 1)[0]


@dataclass(frozen=True)
class CocoSet:
    gts: list[Box]
    preds: list[Box]
    image_video: dict[str, str]   # image id → match id
    class_names: dict[int, str]


def load_coco(gt_path: Path, pred_path: Path | None) -> CocoSet:
    """COCO GT (+ results). Category names map to AD classes when they match, else keep COCO ids.
    Boxes are xywh in COCO and converted to xyxy; ``iscrowd`` GT is dropped."""
    gt = json.loads(Path(gt_path).read_text())
    cat_map = {c["id"]: CLASSES.get(c["name"].lower(), c["id"]) for c in gt["categories"]}
    names = {cat_map[c["id"]]: c["name"] for c in gt["categories"]}
    image_video = {str(i["id"]): video_of(i["file_name"]) for i in gt["images"]}

    def box(a: Mapping[str, Any], score: float) -> Box:
        x, y, w, h = a["bbox"]
        return Box(str(a["image_id"]), cat_map.get(a["category_id"], a["category_id"]), x, y, x + w, y + h, score)

    gts = [box(a, 1.0) for a in gt["annotations"] if not a.get("iscrowd")]
    preds = []
    if pred_path is not None:
        res = json.loads(Path(pred_path).read_text())
        res = res["annotations"] if isinstance(res, dict) else res
        preds = [box(a, float(a.get("score", 1.0))) for a in res]
    return CocoSet(gts, preds, image_video, names)


# --- teams ---------------------------------------------------------------------------------------


def read_team_labels(path: Path) -> dict[tuple[str, int, str], int]:
    data = json.loads(Path(path).read_text())
    if data.get("format") != TEAM_LABELS:
        raise ValueError(f"{path}: expected format {TEAM_LABELS}")
    return {(r["video"], int(r["frame_idx"]), str(r["obj"])): int(r["team"]) for r in data["items"]}
