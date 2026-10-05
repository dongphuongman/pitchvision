"""SP-11 — permissive-licence detectors vs Ultralytics YOLO (G-LEGAL input).

Ultralytics YOLO v8/v11 is AGPL-3.0. Candidates with Apache-2.0 code + weights:
RT-DETRv2 (PekingU) and D-FINE (ustc-community), both COCO-pretrained via HF transformers.
No bbox labels exist yet, so quality is measured as recall against a consensus pseudo-GT
(boxes found by >= 2 of the other detectors) plus visual grids for spot checks.

    python sp11_detectors.py --per-video 10
"""
import argparse
import json
import time
from pathlib import Path

import cv2
import numpy as np
import torch
from transformers import AutoImageProcessor, AutoModelForObjectDetection
from ultralytics import YOLO

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "data" / "golden"
SP09 = Path(__file__).parent / "results" / "sp09"
OUT = Path(__file__).parent / "results" / "sp11"
DEV = "mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu"
PERSON, BALL = 0, 32  # COCO ids (HF COCO configs use the same 0-based ids)
LONG_SIDE = 1280


def wide_frames(per_video: int) -> list[tuple[str, int, np.ndarray]]:
    """Grab per_video wide-shot frames per match (SP-09 labels), resized to 1280px long side."""
    nonwide = json.loads((SP09 / "labels_nonwide.json").read_text())
    index = json.loads((SP09 / "index.json").read_text())
    by_vid: dict[str, list[dict]] = {}
    for e in index:
        if e["k"] not in nonwide.get(e["video"], []):
            by_vid.setdefault(e["video"], []).append(e)
    out = []
    for vid, items in sorted(by_vid.items()):
        picks = [items[i] for i in np.linspace(0, len(items) - 1, min(per_video, len(items))).astype(int)]
        cap = cv2.VideoCapture(str(GOLDEN / "raw" / f"{vid}.mp4"))
        for e in picks:  # single stills, so seeking is fine here (unlike tracking)
            cap.set(cv2.CAP_PROP_POS_MSEC, e["t"] * 1000)
            ok, f = cap.read()
            if ok:
                h, w = f.shape[:2]
                s = LONG_SIDE / max(h, w)
                out.append((vid, e["t"], cv2.resize(f, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)))
        cap.release()
    return out


def sync():
    if DEV == "mps":
        torch.mps.synchronize()
    elif DEV == "cuda":
        torch.cuda.synchronize()


class Yolo:
    def __init__(self, weights: str, imgsz: int):
        self.m, self.imgsz = YOLO(weights), imgsz
        self.name = f"{Path(weights).stem}@{imgsz}"

    def __call__(self, img: np.ndarray, conf: float) -> np.ndarray:
        r = self.m.predict(img, imgsz=self.imgsz, device=DEV, conf=conf, classes=[PERSON, BALL], verbose=False)[0]
        b = r.boxes
        return np.column_stack([b.xyxy.cpu().numpy(), b.conf.cpu().numpy(), b.cls.cpu().numpy()]) if len(b) else np.zeros((0, 6))


class HfDetr:
    def __init__(self, repo: str, size: int):
        self.proc = AutoImageProcessor.from_pretrained(repo)
        self.proc.size = {"height": size, "width": size}
        self.m = AutoModelForObjectDetection.from_pretrained(repo).to(DEV).eval()
        self.name = f"{repo.split('/')[1]}@{size}"

    @torch.no_grad()
    def __call__(self, img: np.ndarray, conf: float) -> np.ndarray:
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        inp = self.proc(images=rgb, return_tensors="pt").to(DEV)
        out = self.m(**inp)
        r = self.proc.post_process_object_detection(out, threshold=conf, target_sizes=[img.shape[:2]])[0]
        lab = r["labels"].cpu().numpy()
        keep = np.isin(lab, [PERSON, BALL])
        return np.column_stack([r["boxes"].cpu().numpy()[keep], r["scores"].cpu().numpy()[keep], lab[keep]]) if keep.any() else np.zeros((0, 6))


def iou(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    if not len(a) or not len(b):
        return np.zeros((len(a), len(b)))
    x1 = np.maximum(a[:, None, 0], b[None, :, 0]); y1 = np.maximum(a[:, None, 1], b[None, :, 1])
    x2 = np.minimum(a[:, None, 2], b[None, :, 2]); y2 = np.minimum(a[:, None, 3], b[None, :, 3])
    inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
    area = lambda z: (z[:, 2] - z[:, 0]) * (z[:, 3] - z[:, 1])
    return inter / (area(a)[:, None] + area(b)[None, :] - inter + 1e-9)


def consensus(others: list[np.ndarray], cls: int, thr: float) -> np.ndarray:
    """Boxes of class cls that >= 2 detectors agree on (greedy cluster on the first detector's boxes, then the rest)."""
    pool = [o[o[:, 5] == cls, :4] for o in others]
    gt = []
    for i, boxes in enumerate(pool):
        for b in boxes:
            if gt and iou(b[None], np.array(gt)).max() >= thr:
                continue
            votes = 1 + sum(1 for j, p in enumerate(pool) if j != i and len(p) and iou(b[None], p).max() >= thr)
            if votes >= 2:
                gt.append(b)
    return np.array(gt).reshape(-1, 4)


def recall(pred: np.ndarray, gt: np.ndarray, thr: float) -> tuple[int, int]:
    if not len(gt):
        return 0, 0
    if not len(pred):
        return 0, len(gt)
    return int((iou(gt, pred).max(1) >= thr).sum()), len(gt)


def grass_mask(img: np.ndarray) -> np.ndarray:
    """Pitch = largest green blob (same HSV idea as SP-09), closed so players inside it count as on-pitch."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, (30, 40, 40), (90, 255, 255))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((31, 31), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m)
    return (lab == 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))) if n > 1 else m > 0


def on_pitch(det: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Keep boxes whose bottom-centre (feet / ball contact) lies on the pitch."""
    if not len(det):
        return det
    h, w = mask.shape
    x = np.clip(((det[:, 0] + det[:, 2]) / 2).astype(int), 0, w - 1)
    y = np.clip((det[:, 3] - 2).astype(int), 0, h - 1)
    return det[mask[y, x]]


def draw(img: np.ndarray, det: np.ndarray, title: str) -> np.ndarray:
    v = img.copy()
    for x1, y1, x2, y2, c, k in det:
        col = (0, 0, 255) if k == BALL else (0, 255, 0)
        cv2.rectangle(v, (int(x1), int(y1)), (int(x2), int(y2)), col, 2 if k == BALL else 1)
    cv2.putText(v, title, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
    return v


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-video", type=int, default=10)
    ap.add_argument("--conf", type=float, default=0.35)
    ap.add_argument("--grids", type=int, default=6, help="frames to render side by side")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    frames = wide_frames(a.per_video)
    print(f"{len(frames)} wide frames from {len({f[0] for f in frames})} matches on {DEV}")
    dets = [
        Yolo("yolo11m.pt", 1280),
        HfDetr("PekingU/rtdetr_v2_r50vd", 640),
        HfDetr("PekingU/rtdetr_v2_r50vd", 1280),
        HfDetr("ustc-community/dfine-medium-obj2coco", 640),
        HfDetr("ustc-community/dfine-medium-obj2coco", 1280),
        HfDetr("ustc-community/dfine-large-obj2coco-e25", 1280),
    ]
    preds: dict[str, list[np.ndarray]] = {}
    speed: dict[str, float] = {}
    for d in dets:
        d(frames[0][2], a.conf); sync()  # warm-up
        t0, ps = time.perf_counter(), []
        for _, _, img in frames:
            ps.append(d(img, a.conf))
        sync()
        speed[d.name] = len(frames) / (time.perf_counter() - t0)
        preds[d.name] = ps
        print(f"{d.name:40s} {speed[d.name]:5.1f} fps")

    masks = [grass_mask(img) for _, _, img in frames]
    raw_persons = {n: round(float(np.mean([(p[:, 5] == PERSON).sum() for p in preds[n]])), 1) for n in preds}
    preds = {n: [on_pitch(p, m) for p, m in zip(ps, masks)] for n, ps in preds.items()}
    names = list(preds)
    family = lambda n: n.split("@")[0].split("-")[0].split("_")[0]  # yolo11m / rtdetr / dfine
    rows = {}
    for n in names:
        others = [m for m in names if family(m) != family(n)]  # no self-family votes
        hit_p = tot_p = hit_s = tot_s = hit_b = tot_b = 0
        n_p, ball_frames = [], 0
        for i in range(len(frames)):
            p = preds[n][i]
            gp = consensus([preds[m][i] for m in others], PERSON, 0.5)
            gb = consensus([preds[m][i] for m in others], BALL, 0.3)
            h, t = recall(p[p[:, 5] == PERSON, :4], gp, 0.5); hit_p += h; tot_p += t
            small = gp[(gp[:, 3] - gp[:, 1]) < 40] if len(gp) else gp
            h, t = recall(p[p[:, 5] == PERSON, :4], small, 0.5); hit_s += h; tot_s += t
            h, t = recall(p[p[:, 5] == BALL, :4], gb, 0.3); hit_b += h; tot_b += t
            n_p.append(int((p[:, 5] == PERSON).sum())); ball_frames += int((p[:, 5] == BALL).any())
        rows[n] = {
            "fps_" + DEV: round(speed[n], 1),
            "persons_per_frame_all": raw_persons[n],
            "persons_per_frame_on_pitch": round(float(np.mean(n_p)), 1),
            "person_recall_vs_consensus": round(hit_p / max(tot_p, 1), 3),
            "small_person_recall(<40px)": round(hit_s / max(tot_s, 1), 3),
            "ball_frames_pct": round(ball_frames / len(frames), 3),
            "ball_recall_vs_consensus": round(hit_b / max(tot_b, 1), 3) if tot_b else None,
        }
    res = {"device": DEV, "frames": len(frames), "conf": a.conf, "detectors": rows}
    (OUT / "sp11_results.json").write_text(json.dumps(res, indent=2))
    print(json.dumps(rows, indent=2))

    for gi, i in enumerate(np.linspace(0, len(frames) - 1, a.grids).astype(int)):
        vid, t, img = frames[i]
        tiles = [draw(img, preds[n][i], n) for n in names]
        tiles += [np.zeros_like(img)] * (-len(tiles) % 2)
        grid = np.vstack([np.hstack(tiles[k:k + 2]) for k in range(0, len(tiles), 2)])
        cv2.imwrite(str(OUT / f"grid_{gi}_{vid}_{t}.jpg"), cv2.resize(grid, None, fx=0.6, fy=0.6), [cv2.IMWRITE_JPEG_QUALITY, 85])


if __name__ == "__main__":
    main()
