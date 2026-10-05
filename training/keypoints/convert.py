"""Pitch keypoint datasets → ``pv-pitch-keypoints/v1`` (our label format, also read by eval/run_homography.py)
→ Ultralytics YOLO-pose dataset. Keypoint index = ``cv_core.pitch.template`` (32 points, roboflow order).

    # our labels (CVAT "CVAT for images 1.1" XML, points or skeleton; or Label Studio JSON export)
    python training/keypoints/convert.py cvat        annotations.xml        --out data/kp/ours.json
    python training/keypoints/convert.py labelstudio export.json            --out data/kp/ours.json
    # SoccerNet calibration (per-image JSON of line points). Research-only licence: README → G-LEGAL
    python training/keypoints/convert.py soccernet   /data/sn-calib/train   --out data/kp/sn_train.json
    # frames for labelling / for labels that reference golden videos (decoded like S2: 1280 px, 25 fps)
    python training/keypoints/convert.py frames      --sp09 --out-dir /secure/frames      (or: --labels x.json)
    # → YOLO-pose dataset (images symlinked, labels txt, data.yaml); split by video
    python training/keypoints/convert.py yolo data/kp/ours.json data/kp/sn_train.json --images-root ... \
        --out data/kp/yolo --val 0.15

Each frame row: ``{"video", "t_ms", "width", "height", "image"?, "keypoints": [[idx, x, y, v?], ...]}`` with
v = 2 visible, 1 labelled but occluded (default 2). Keypoints outside the frame are not listed.

``yolo --complete-from-h`` (default on): when a frame has ≥ 6 well-spread labelled points that a homography fits
within ``--complete-max-px``, every other template keypoint inside the frame is filled in from that H (v=2). This
gives the precise position of points that are hard to click (circle extremes 30/31, virtual 10/11/18/19, spots)
and keeps every in-frame point supervised. Points outside the frame get v=0 → Ultralytics trains their
confidence towards 0: the out-of-frame penalty of M1-CV-19 (SP-03 cause 1: hallucinated border points).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import numpy as np
from cv_core.pitch import FLIP_INDEX, KEYPOINT_NAMES, NUM_KEYPOINTS, FIFA_105x68, PitchTemplate, fit_homography, project

FORMAT = "pv-pitch-keypoints/v1"
NAME_TO_IDX = {n: i for i, n in enumerate(KEYPOINT_NAMES)}
Frame = dict[str, Any]


def kp_index(label: str) -> int | None:
    """Keypoint label → index: template name (``corner_tl``), ``kp07`` / ``7`` / ``07`` (0-based)."""
    s = label.strip()
    if s in NAME_TO_IDX:
        return NAME_TO_IDX[s]
    m = re.fullmatch(r"(?:kp)?_?(\d{1,2})", s, re.IGNORECASE)
    if m and int(m.group(1)) < NUM_KEYPOINTS:
        return int(m.group(1))
    return None


def video_time(name: str) -> tuple[str, int]:
    """Image file name → (video, t_ms). ``{video}_{t_ms:07d}.jpg`` (``frames`` output) or
    ``{video}/{frame_idx:06d}.jpg`` (detection convention, 25 fps); else (stem, 0)."""
    p = Path(name)
    if re.fullmatch(r"\d{6}", p.stem) and p.parent.name:
        return p.parent.name, int(p.stem) * 40
    m = re.fullmatch(r"(.+)_(\d{7})", p.stem)
    if m:
        return m.group(1), int(m.group(2))
    return p.stem, 0


def write_labels(path: Path, frames: list[Frame], **meta: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"format": FORMAT, "template": "fifa-105x68", **meta, "frames": frames}, indent=1))
    print(f"wrote {path}: {len(frames)} frames, {sum(len(f['keypoints']) for f in frames)} keypoints")


def read_labels(path: Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text())
    if data.get("format") != FORMAT:
        raise SystemExit(f"{path}: not {FORMAT}")
    return data


# --- CVAT / Label Studio ------------------------------------------------------------------------------


def from_cvat(xml_path: Path) -> list[Frame]:
    """CVAT for images 1.1: ``<image name width height>`` with ``<points label=…>`` (one per keypoint) or a
    ``<skeleton>`` of ``<points>``. ``outside="1"`` = not visible (skipped), ``occluded="1"`` → v=1."""
    root = ET.parse(xml_path).getroot()
    frames = []
    for img in root.iter("image"):
        kps: dict[int, list[float]] = {}
        for pt in img.iter("points"):
            idx = kp_index(pt.get("label", ""))
            if idx is None or pt.get("outside") == "1":
                continue
            x, y = (float(v) for v in pt.get("points", "").split(";")[0].split(","))
            kps[idx] = [x, y, 1.0 if pt.get("occluded") == "1" else 2.0]
        name = img.get("name", "")
        video, t = video_time(name)
        frames.append({"video": video, "t_ms": t, "width": int(img.get("width", 0)),
                       "height": int(img.get("height", 0)),
                       "image": name, "keypoints": [[i, *v] for i, v in sorted(kps.items())]})
    return frames


def from_labelstudio(json_path: Path) -> list[Frame]:
    """Label Studio JSON export, KeyPointLabels (x/y in percent); the label text = keypoint name or index."""
    frames = []
    for task in json.loads(json_path.read_text()):
        anns = task.get("annotations") or []
        if not anns:
            continue
        kps: dict[int, list[float]] = {}
        w = h = 0
        for r in anns[0].get("result", []):
            if r.get("type") != "keypointlabels":
                continue
            w, h = int(r["original_width"]), int(r["original_height"])
            val = r["value"]
            idx = kp_index((val.get("keypointlabels") or [""])[0])
            if idx is not None:
                kps[idx] = [val["x"] / 100 * w, val["y"] / 100 * h, 2.0]
        image = str(task.get("data", {}).get("image", "")).split("/")[-1].split("?")[0]
        image = re.sub(r"^[0-9a-f]{8}-", "", image)  # LS upload prefix
        video, t = video_time(image)
        frames.append({"video": video, "t_ms": t, "width": w, "height": h, "image": image,
                       "keypoints": [[i, *v] for i, v in sorted(kps.items())]})
    return frames


# --- SoccerNet calibration ------------------------------------------------------------------------------

# keypoint → (line A, line B) whose intersection defines it (SoccerNet "left"/"top" = image left / far side).
SN_INTERSECTIONS: dict[int, tuple[str, str]] = {
    0: ("Side line left", "Side line top"), 1: ("Side line left", "Big rect. left top"),
    2: ("Side line left", "Small rect. left top"), 3: ("Side line left", "Small rect. left bottom"),
    4: ("Side line left", "Big rect. left bottom"), 5: ("Side line left", "Side line bottom"),
    6: ("Small rect. left main", "Small rect. left top"), 7: ("Small rect. left main", "Small rect. left bottom"),
    9: ("Big rect. left main", "Big rect. left top"), 10: ("Big rect. left main", "Small rect. left top"),
    11: ("Big rect. left main", "Small rect. left bottom"), 12: ("Big rect. left main", "Big rect. left bottom"),
    13: ("Middle line", "Side line top"), 16: ("Middle line", "Side line bottom"),
    17: ("Big rect. right main", "Big rect. right top"), 18: ("Big rect. right main", "Small rect. right top"),
    19: ("Big rect. right main", "Small rect. right bottom"), 20: ("Big rect. right main", "Big rect. right bottom"),
    22: ("Small rect. right main", "Small rect. right top"), 23: ("Small rect. right main", "Small rect. right bottom"),
    24: ("Side line right", "Side line top"), 25: ("Side line right", "Big rect. right top"),
    26: ("Side line right", "Small rect. right top"), 27: ("Side line right", "Small rect. right bottom"),
    28: ("Side line right", "Big rect. right bottom"), 29: ("Side line right", "Side line bottom"),
}


def _fit_line(pts: np.ndarray) -> np.ndarray | None:
    """Homogeneous line through ≥ 2 points (total least squares)."""
    if len(pts) < 2:
        return None
    c = pts.mean(0)
    _, _, vt = np.linalg.svd(pts - c)
    d = vt[0]
    n = np.array([-d[1], d[0]])
    return np.array([n[0], n[1], -n @ c])


def _fit_conic(pts: np.ndarray) -> np.ndarray | None:
    """Conic ax²+bxy+cy²+dx+ey+f=0 through ≥ 5 points (algebraic least squares, normalised coords)."""
    if len(pts) < 5:
        return None
    x, y = pts[:, 0], pts[:, 1]
    A = np.c_[x * x, x * y, y * y, x, y, np.ones_like(x)]
    _, _, vt = np.linalg.svd(A)
    return vt[-1]


def _line_conic(line: np.ndarray, conic: np.ndarray) -> list[np.ndarray]:
    a, b, c = line
    d = np.array([-b, a])  # direction
    p0 = -c * np.array([a, b]) / (a * a + b * b)  # closest point to origin
    A, B, C, D, E, F = conic
    px, py, dx, dy = p0[0], p0[1], d[0], d[1]
    qa = A * dx * dx + B * dx * dy + C * dy * dy
    qb = 2 * A * px * dx + B * (px * dy + py * dx) + 2 * C * py * dy + D * dx + E * dy
    qc = A * px * px + B * px * py + C * py * py + D * px + E * py + F
    disc = qb * qb - 4 * qa * qc
    if abs(qa) < 1e-12 or disc < 0:
        return []
    r = math.sqrt(disc)
    return [p0 + t * d for t in ((-qb - r) / (2 * qa), (-qb + r) / (2 * qa))]


def soccernet_keypoints(lines: dict[str, list[dict[str, float]]], w: int, h: int) -> dict[int, list[float]]:
    """One SoccerNet calibration annotation (normalised line points) → {idx: [x, y, 2]} in pixels, in-frame only."""
    pts = {k: np.array([[p["x"] * w, p["y"] * h] for p in v], float) for k, v in lines.items() if v}
    fitted = {k: _fit_line(v) for k, v in pts.items() if not k.startswith("Circle") and not k.startswith("Goal")}
    out: dict[int, list[float]] = {}
    for idx, (la, lb) in SN_INTERSECTIONS.items():
        A, B = fitted.get(la), fitted.get(lb)
        if A is None or B is None:
            continue
        X = np.cross(A, B)
        if abs(X[2]) < 1e-9:
            continue
        x, y = X[:2] / X[2]
        if 0 <= x < w and 0 <= y < h:
            out[idx] = [float(x), float(y), 2.0]
    mid, circ = fitted.get("Middle line"), pts.get("Circle central")
    if mid is not None and circ is not None:
        s = max(w, h)
        conic = _fit_conic(circ / s)
        if conic is not None:
            ln = mid.copy()
            ln[2] /= s
            xs = sorted((p * s for p in _line_conic(ln, conic)), key=lambda p: p[1])  # far (top) first
            if len(xs) == 2:
                for idx, p in zip((14, 15), xs, strict=True):
                    if 0 <= p[0] < w and 0 <= p[1] < h:
                        out[idx] = [float(p[0]), float(p[1]), 2.0]
    return out


def from_soccernet(root: Path, width: int = 960, height: int = 540) -> list[Frame]:
    """SoccerNet calibration split dir: ``NNNNN.jpg`` + ``NNNNN.json`` (line class → [{x, y}] normalised).
    Only converts files already on disk — this script never downloads SoccerNet."""
    frames = []
    for jp in sorted(root.glob("*.json")):
        if jp.name in ("match_info.json", "per_match_info.json"):
            continue
        img = jp.with_suffix(".jpg")
        w, h = width, height
        if img.is_file():
            import cv2

            im = cv2.imread(str(img))
            if im is not None:
                h, w = im.shape[:2]
        kps = soccernet_keypoints(json.loads(jp.read_text()), w, h)
        frames.append({"video": f"sn_{root.name}", "t_ms": int(jp.stem) if jp.stem.isdigit() else 0, "width": w,
                       "height": h, "image": img.name, "keypoints": [[i, *v] for i, v in sorted(kps.items())]})
    return frames


# --- completion + YOLO export ----------------------------------------------------------------------------


def complete_from_h(frame: Frame, template: PitchTemplate = FIFA_105x68, *, min_points: int = 6,
                    max_px: float = 4.0) -> tuple[Frame, bool]:
    """Fill every in-frame template keypoint from a homography fitted to the labelled ones (see module doc)."""
    kps = {int(k[0]): k[1:] for k in frame["keypoints"]}
    if len(kps) < min_points:
        return frame, False
    idx = sorted(kps)
    src = template.keypoints[idx]
    if np.linalg.svd(src - src.mean(0), compute_uv=False)[1] / math.sqrt(len(idx)) < 3.0:
        return frame, False
    dst = np.array([kps[i][:2] for i in idx], float)
    H, _ = fit_homography(src, dst)
    if np.linalg.norm(project(H, src) - dst, axis=1).max() > max_px:
        return frame, False  # labels disagree with a plane (mislabel / non-standard pitch): keep as is
    w, h = frame["width"], frame["height"]
    allp = project(H, template.keypoints)
    hom = np.c_[template.keypoints, np.ones(NUM_KEYPOINTS)] @ H.T
    front = np.sign(hom[:, 2]) == np.sign(hom[idx, 2].mean())
    out = dict(kps)
    for i, (x, y) in enumerate(allp):
        if i not in out and front[i] and 0 <= x < w and 0 <= y < h:
            out[i] = [float(x), float(y), 2.0]
    return {**frame, "keypoints": [[i, *v] for i, v in sorted(out.items())]}, True


def yolo_line(frame: Frame, template: PitchTemplate = FIFA_105x68) -> str | None:
    """``0 cx cy w h x1 y1 v1 … x32 y32 v32`` normalised. Box = the visible pitch (outline through the labels'
    homography, clipped to the frame) or the labelled points' bounding box (+5 %)."""
    w, h = frame["width"], frame["height"]
    kps = {int(k[0]): k[1:] for k in frame["keypoints"]}
    if not kps or not w or not h:
        return None
    xy = np.array([v[:2] for v in kps.values()], float)
    x0, y0 = xy.min(0)
    x1, y1 = xy.max(0)
    if len(kps) >= 4:
        idx = sorted(kps)
        src = template.keypoints[idx]
        if np.linalg.svd(src - src.mean(0), compute_uv=False)[1] / math.sqrt(len(idx)) >= 1.5:
            H, _ = fit_homography(src, np.array([kps[i][:2] for i in idx], float))
            L, W = template.length, template.width
            edge = np.concatenate([np.linspace((0, 0), (L, 0), 50), np.linspace((L, 0), (L, W), 50),
                                   np.linspace((L, W), (0, W), 50), np.linspace((0, W), (0, 0), 50)])
            hom = np.c_[edge, np.ones(len(edge))] @ H.T
            ok = np.sign(hom[:, 2]) == np.sign(hom[:, 2].mean())
            p = hom[ok, :2] / hom[ok, 2:3]
            if len(p):
                x0, y0 = min(x0, p[:, 0].min()), min(y0, p[:, 1].min())
                x1, y1 = max(x1, p[:, 0].max()), max(y1, p[:, 1].max())
    pad = 0.05 * max(x1 - x0, y1 - y0)
    x0, y0, x1, y1 = max(0, x0 - pad), max(0, y0 - pad), min(w, x1 + pad), min(h, y1 + pad)
    vals = [f"0 {(x0 + x1) / 2 / w:.6f} {(y0 + y1) / 2 / h:.6f} {(x1 - x0) / w:.6f} {(y1 - y0) / h:.6f}"]
    for i in range(NUM_KEYPOINTS):
        if i in kps and 0 <= kps[i][0] < w and 0 <= kps[i][1] < h:
            v = int(kps[i][2]) if len(kps[i]) > 2 else 2
            vals.append(f"{kps[i][0] / w:.6f} {kps[i][1] / h:.6f} {v}")
        else:
            vals.append("0 0 0")  # not labelled / outside the frame → conf target 0
    return " ".join(vals)


def data_yaml(out: Path) -> str:
    return (f"# PitchVision pitch keypoints (cv_core.pitch.template, 32 pts)\npath: {out.resolve()}\n"
            "train: images/train\nval: images/val\nkpt_shape: [32, 3]\n"
            f"flip_idx: {list(FLIP_INDEX)}\nnames:\n  0: pitch\n")


def split_by_video(frames: Sequence[Frame], val: float, seed: int = 0) -> tuple[list[Frame], list[Frame]]:
    """Whole videos go to val (no near-duplicate frames across the split)."""
    vids = sorted({f["video"] for f in frames})
    random.Random(seed).shuffle(vids)
    n_val = max(1, round(len(vids) * val)) if val > 0 and len(vids) > 1 else 0
    vset = set(vids[:n_val])
    return [f for f in frames if f["video"] not in vset], [f for f in frames if f["video"] in vset]


def to_yolo(label_files: Iterable[Path], images_roots: Sequence[Path], out: Path, *, val: float, complete: bool,
            max_px: float, copy: bool) -> None:
    frames: list[Frame] = []
    for lf in label_files:
        frames += read_labels(lf)["frames"]
    n_comp = 0
    if complete:
        done = [complete_from_h(f, max_px=max_px) for f in frames]
        frames, n_comp = [f for f, _ in done], sum(c for _, c in done)
    train, valid = split_by_video(frames, val)
    n_img = 0
    for split, fs in (("train", train), ("val", valid)):
        (out / "images" / split).mkdir(parents=True, exist_ok=True)
        (out / "labels" / split).mkdir(parents=True, exist_ok=True)
        for f in fs:
            src = next((r / f["image"] for r in images_roots if f.get("image") and (r / f["image"]).is_file()), None)
            if src is None:
                src = next((r / f"{f['video']}_{f['t_ms']:07d}.jpg" for r in images_roots
                            if (r / f"{f['video']}_{f['t_ms']:07d}.jpg").is_file()), None)
            line = yolo_line(f)
            if src is None or line is None:
                continue
            stem = f"{f['video']}_{f['t_ms']:07d}_{Path(f.get('image') or 'x').stem}"[:120]
            dst = out / "images" / split / f"{stem}{src.suffix}"
            if dst.exists() or dst.is_symlink():
                dst.unlink()
            if copy:
                dst.write_bytes(src.read_bytes())
            else:
                os.symlink(src.resolve(), dst)
            (out / "labels" / split / f"{stem}.txt").write_text(line + "\n")
            n_img += 1
    (out / "data.yaml").write_text(data_yaml(out))
    print(f"YOLO dataset {out}: {n_img} images ({len(train)} train / {len(valid)} val frames before image lookup), "
          f"{n_comp} completed from H")


def extract_frames(keys: Sequence[tuple[str, int]], golden_dir: Path, out_dir: Path, long_side: int) -> None:
    """Decode golden frames like S2 (25 fps timeline, long side ``long_side``) → ``{video}_{t_ms:07d}.jpg``."""
    import cv2
    from cv_core.io import VideoReader

    repo = Path(__file__).resolve().parents[2]
    o = out_dir.resolve()
    if o == repo or (repo in o.parents and "data" not in o.relative_to(repo).parts):
        raise SystemExit(f"{o}: broadcast frames must stay out of the repo (use training/**/data/ or a dir outside)")
    out_dir.mkdir(parents=True, exist_ok=True)
    by_video: dict[str, list[int]] = defaultdict(list)
    for v, t in keys:
        by_video[v].append(t)
    n = 0
    for v, ts in sorted(by_video.items()):
        path = golden_dir / "raw" / f"{v}.mp4"
        if not path.is_file():
            print(f"skip {v}: {path} missing", file=sys.stderr)
            continue
        for t in sorted(ts):
            fr = next(iter(VideoReader(path, t / 1000, t / 1000 + 0.04, target_fps=25, long_side=long_side)), None)
            if fr is not None:
                cv2.imwrite(str(out_dir / f"{v}_{t:07d}.jpg"), fr.image, [cv2.IMWRITE_JPEG_QUALITY, 95])
                n += 1
    print(f"wrote {n} frames to {out_dir}")


def sp09_wide(sp09_dir: Path) -> list[tuple[str, int]]:
    index = json.loads((sp09_dir / "index.json").read_text())
    nonwide = json.loads((sp09_dir / "labels_nonwide.json").read_text())
    return [(it["video"], int(it["t"]) * 1000) for it in index
            if it["video"] in nonwide and it["k"] not in nonwide[it["video"]]]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("cvat", "labelstudio", "soccernet"):
        p = sub.add_parser(name)
        p.add_argument("src", type=Path)
        p.add_argument("--out", type=Path, required=True)
    y = sub.add_parser("yolo")
    y.add_argument("labels", type=Path, nargs="+")
    y.add_argument("--images-root", type=Path, nargs="+", required=True)
    y.add_argument("--out", type=Path, required=True)
    y.add_argument("--val", type=float, default=0.15)
    y.add_argument("--no-complete", action="store_true")
    y.add_argument("--complete-max-px", type=float, default=4.0)
    y.add_argument("--copy", action="store_true", help="copy images instead of symlinking")
    fr = sub.add_parser("frames")
    g = fr.add_mutually_exclusive_group(required=True)
    g.add_argument("--labels", type=Path)
    g.add_argument("--sp09", action="store_true")
    fr.add_argument("--sp09-dir", type=Path, default=Path(__file__).resolve().parents[2] / "spikes/results/sp09")
    fr.add_argument("--golden-dir", type=Path,
                    default=Path(os.environ.get("PITCHVISION_GOLDEN_DIR",
                                                Path(__file__).resolve().parents[2] / "data" / "golden")))
    fr.add_argument("--out-dir", type=Path, required=True)
    fr.add_argument("--long-side", type=int, default=1280)
    a = ap.parse_args()

    if a.cmd == "cvat":
        write_labels(a.out, from_cvat(a.src), source=f"cvat:{a.src.name}")
    elif a.cmd == "labelstudio":
        write_labels(a.out, from_labelstudio(a.src), source=f"labelstudio:{a.src.name}")
    elif a.cmd == "soccernet":
        write_labels(a.out, from_soccernet(a.src), source=f"soccernet:{a.src.name}",
                     licence="SoccerNet research-only (G-LEGAL)")
    elif a.cmd == "yolo":
        to_yolo(a.labels, a.images_root, a.out, val=a.val, complete=not a.no_complete, max_px=a.complete_max_px,
                copy=a.copy)
    elif a.cmd == "frames":
        keys = sp09_wide(a.sp09_dir) if a.sp09 else [(f["video"], f["t_ms"]) for f in read_labels(a.labels)["frames"]]
        extract_frames(keys, a.golden_dir, a.out_dir, a.long_side)


if __name__ == "__main__":
    main()
