"""SP-05 — label-free tracking baseline on broadcast: how badly do IDs fragment, and how much is caused by camera cuts?

ByteTrack (Ultralytics) on 90 s windows resampled to 25 fps. Cuts from HSV-histogram correlation drops.
Fragmentation = unique track IDs / median players visible. 'Cut-caused' = tracks born within 1 s after a cut.
"""
import json
from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLO

ROOT = Path(__file__).resolve().parents[1]
R = Path(__file__).parent / "results" / "sp05"
CLIPS = [("MZjaWGwbtEc", 200), ("TyCCa3tyhZw", 300)]
WINDOW_S, FPS = 90, 25
import sys
TRACKER = sys.argv[1] if len(sys.argv) > 1 else "bytetrack.yaml"


def hist(f: np.ndarray) -> np.ndarray:
    h = cv2.calcHist([cv2.cvtColor(f, cv2.COLOR_BGR2HSV)], [0, 1], None, [16, 8], [0, 180, 0, 256])
    return cv2.normalize(h, h).flatten()


def main() -> None:
    R.mkdir(parents=True, exist_ok=True)
    out = {}
    for vid, start in CLIPS:
        model = YOLO("yolo11m.pt")  # fresh tracker state per clip
        cap = cv2.VideoCapture(str(ROOT / "data" / "golden" / "raw" / f"{vid}.mp4"))
        births, deaths, last_seen, visible, cuts = {}, {}, {}, [], []
        prev_h = None
        cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000)  # one seek, then sequential decode
        src_fps = cap.get(cv2.CAP_PROP_FPS)
        n_src, i = 0, 0
        while i < WINDOW_S * FPS:
            ok, f = cap.read()
            if not ok:
                break
            n_src += 1
            if (n_src - 1) / src_fps < i / FPS:  # resample source fps → 25 fps
                continue
            h = hist(f)
            if prev_h is not None and cv2.compareHist(prev_h, h, cv2.HISTCMP_CORREL) < 0.6:
                cuts.append(i)
            prev_h = h
            r = model.track(f, persist=True, tracker=TRACKER, imgsz=960, conf=0.35,
                            classes=[0], device="mps", verbose=False)[0]
            ids = r.boxes.id.int().tolist() if r.boxes.id is not None else []
            visible.append(len(ids))
            for t in ids:
                births.setdefault(t, i)
                last_seen[t] = i
            i += 1
        cap.release()
        life = np.array([(last_seen[t] - births[t] + 1) / FPS for t in births])
        near_cut = lambda fr: any(0 <= fr - c <= FPS for c in cuts)
        born_after_cut = sum(near_cut(b) for b in births.values())
        med_vis = float(np.median([v for v in visible if v > 0])) if any(visible) else 0
        out[vid] = {
            "seconds": WINDOW_S, "cuts": len(cuts), "unique_ids": len(births),
            "median_visible": med_vis, "fragmentation": round(len(births) / max(med_vis, 1), 1),
            "median_track_s": round(float(np.median(life)), 1), "tracks_over_10s": int((life > 10).sum()),
            "born_within_1s_after_cut": round(born_after_cut / max(len(births), 1), 3),
        }
        print(vid, out[vid])
    (R / f"summary_{Path(TRACKER).stem}.json").write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
