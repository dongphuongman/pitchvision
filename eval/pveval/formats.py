"""Label / prediction file formats read by the eval scripts (see eval/README.md).

Shots      ``{"format": "pv-shot-samples/v1", "samples": [{"video", "t_ms", "label", "conf"?}]}``
           or ``{"format": "pv-shot-timelines/v1", "timelines": {video: {"shots": [Shot…]}}}``
           (pitchvision_schemas.ShotTimeline; sampled at the GT timestamps).
Detection  COCO instances JSON for GT (CVAT "COCO 1.0" export) + COCO results list for predictions.
           Image ``file_name`` = ``{video_id}/{frame_idx:06d}.jpg`` (or ``{video_id}_{frame_idx:06d}.jpg``).
Teams      ``{"format": "pv-team-labels/v1", "items": [{"video", "frame_idx", "obj", "team"}]}`` for both
           GT and predictions, joined on (video, frame_idx, obj); team 0/1, -1 = not a team player.
Pitch      ``{"format": "pv-pitch-keypoints/v1", "frames": [{"video", "t_ms"|"frame_idx", "width", "height",
           "keypoints": [[idx, x, y], …]}], "pitch_size"?: {video: [L, W]}}`` (GT, cv_core.pitch.template index);
           predictions ``{"format": "pv-homographies/v1", "frames": [{"video", "t_ms", "width", "height",
           "H": 3×3 image→pitch | null, "h_quality"?, "reason"?}]}``.
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


# --- pitch keypoints / homographies ---------------------------------------------------------------

PITCH_KEYPOINTS = "pv-pitch-keypoints/v1"
HOMOGRAPHIES = "pv-homographies/v1"
DEFAULT_PITCH = (105.0, 68.0)


@dataclass(frozen=True)
class PitchLabel:
    """GT for one frame: visible template keypoints (cv_core.pitch.template index) in pixels of a
    ``width × height`` frame on the normalised timeline; optional real pitch size of the venue."""

    video: str
    t_ms: int
    width: int
    height: int
    points: dict[int, tuple[float, float]]
    pitch: tuple[float, float] = DEFAULT_PITCH

    def scaled(self, width: int, height: int) -> dict[int, tuple[float, float]]:
        sx, sy = width / self.width, height / self.height
        return {i: (x * sx, y * sy) for i, (x, y) in self.points.items()}


def _t_ms(r: Mapping[str, Any]) -> int:
    if "t_ms" in r:
        return int(r["t_ms"])
    return int(r["frame_idx"]) * 40  # frame_idx on the normalised 25 fps timeline


def read_pitch_labels(path: Path) -> list[PitchLabel]:
    data = json.loads(Path(path).read_text())
    if data.get("format") != PITCH_KEYPOINTS:
        raise ValueError(f"{path}: expected format {PITCH_KEYPOINTS}")
    sizes = {v: (float(s[0]), float(s[1])) for v, s in data.get("pitch_size", {}).items()}
    out = []
    for r in data["frames"]:
        pts = {int(i): (float(x), float(y)) for i, x, y in r["keypoints"]}
        out.append(PitchLabel(r["video"], _t_ms(r), int(r["width"]), int(r["height"]), pts,
                              sizes.get(r["video"], DEFAULT_PITCH)))
    return out


def write_pitch_labels(path: Path, labels: list[PitchLabel], **meta: Any) -> None:
    sizes = {lb.video: list(lb.pitch) for lb in labels if lb.pitch != DEFAULT_PITCH}
    frames = [{"video": lb.video, "t_ms": lb.t_ms, "width": lb.width, "height": lb.height,
               "keypoints": [[i, round(x, 2), round(y, 2)] for i, (x, y) in sorted(lb.points.items())]}
              for lb in labels]
    doc: dict[str, Any] = {"format": PITCH_KEYPOINTS, "template": "fifa-105x68", **meta, "frames": frames}
    if sizes:
        doc["pitch_size"] = sizes
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1))


@dataclass(frozen=True)
class HomographyPred:
    video: str
    t_ms: int
    width: int
    height: int
    H: list[list[float]] | None   # image → pitch metres, in pixels of width × height
    h_quality: float | None = None
    reason: str = ""


def read_homographies(path: Path) -> dict[tuple[str, int], HomographyPred]:
    data = json.loads(Path(path).read_text())
    if data.get("format") != HOMOGRAPHIES:
        raise ValueError(f"{path}: expected format {HOMOGRAPHIES}")
    return {(r["video"], _t_ms(r)): HomographyPred(r["video"], _t_ms(r), int(r["width"]), int(r["height"]), r["H"],
                                                   r.get("h_quality"), r.get("reason", ""))
            for r in data["frames"]}


def write_homographies(path: Path, preds: list[HomographyPred], **meta: Any) -> None:
    frames = [{"video": p.video, "t_ms": p.t_ms, "width": p.width, "height": p.height, "H": p.H,
               "h_quality": p.h_quality, "reason": p.reason} for p in preds]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"format": HOMOGRAPHIES, **meta, "frames": frames}, indent=1))
