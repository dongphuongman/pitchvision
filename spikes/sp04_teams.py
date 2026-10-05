"""SP-04 — unsupervised team split per video: torso crops → SigLIP → UMAP → KMeans(2), vs an HSV-histogram baseline.

Uses only wide-angle frames (SP-09 ground truth). Writes a montage per video (cluster A | cluster B)
for visual scoring, plus agreement between the two methods.
"""
import json
import random
from pathlib import Path

import cv2
import numpy as np
import torch
import umap
from sklearn.cluster import KMeans
from transformers import AutoProcessor, SiglipVisionModel
from ultralytics import YOLO

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "data" / "golden"
R = Path(__file__).parent / "results" / "sp04"
NONWIDE = json.loads((Path(__file__).parent / "results" / "sp09" / "labels_nonwide.json").read_text())
SIGLIP = "google/siglip-base-patch16-224"
DEV = "mps" if torch.backends.mps.is_available() else "cpu"


def torso(frame: np.ndarray, box: np.ndarray) -> np.ndarray | None:
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    c = frame[int(y1 + 0.18 * h):int(y1 + 0.55 * h), int(x1 + 0.22 * w):int(x2 - 0.22 * w)]
    return c if c.size and c.shape[0] >= 8 and c.shape[1] >= 5 else None


def hsv_feature(c: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(c, cv2.COLOR_BGR2HSV)
    grass = cv2.inRange(hsv, (30, 40, 40), (90, 255, 255))
    h = cv2.calcHist([hsv], [0, 1, 2], cv2.bitwise_not(grass), [12, 4, 4], [0, 180, 0, 256, 0, 256]).flatten()
    return h / (h.sum() + 1e-6)


def agreement(a: np.ndarray, b: np.ndarray) -> float:
    m = (a == b).mean()
    return max(m, 1 - m)


def main() -> None:
    R.mkdir(parents=True, exist_ok=True)
    det = YOLO("yolo11m.pt")
    proc = AutoProcessor.from_pretrained(SIGLIP)
    enc = SiglipVisionModel.from_pretrained(SIGLIP).to(DEV).eval()
    rows, summary = [], {}
    for m in json.loads((GOLDEN / "manifest.json").read_text()):
        if not m["downloaded"]:
            continue
        vid = m["id"]
        cap = cv2.VideoCapture(str(GOLDEN / m["file"]))
        crops = []
        for k in [k for k in range(45) if k not in NONWIDE[vid]][:30]:
            cap.set(cv2.CAP_PROP_POS_MSEC, (5 + 20 * k) * 1000)
            ok, f = cap.read()
            if not ok:
                continue
            r = det.predict(f, imgsz=1280, conf=0.4, classes=[0], device=DEV, verbose=False)[0]
            H = f.shape[0]
            for b in r.boxes.xyxy.cpu().numpy():
                if 0.03 * H < b[3] - b[1] < 0.25 * H and b[1] > 0.08 * H:  # players, not crowd/ads at the top
                    c = torso(f, b)
                    if c is not None:
                        crops.append(c)
        cap.release()

        with torch.no_grad():
            emb = []
            for i in range(0, len(crops), 64):
                batch = [cv2.cvtColor(c, cv2.COLOR_BGR2RGB) for c in crops[i:i + 64]]
                inp = proc(images=batch, return_tensors="pt").to(DEV)
                emb.append(enc(**inp).pooler_output.cpu().numpy())
        emb = np.concatenate(emb)
        red = umap.UMAP(n_components=3, random_state=0).fit_transform(emb)
        lab_sig = KMeans(2, n_init=10, random_state=0).fit_predict(red)
        lab_hsv = KMeans(2, n_init=10, random_state=0).fit_predict(np.array([hsv_feature(c) for c in crops]))
        summary[vid] = {"crops": len(crops), "cluster_share": round(float(lab_sig.mean()), 3),
                        "siglip_vs_hsv_agreement": round(float(agreement(lab_sig, lab_hsv)), 3)}
        print(vid, summary[vid])

        random.seed(0)
        tiles = []
        for c_id in (0, 1):
            idx = [i for i in range(len(crops)) if lab_sig[i] == c_id]
            pick = random.sample(idx, min(12, len(idx)))
            tiles += [cv2.resize(crops[i], (36, 48)) for i in pick] + [np.zeros((48, 36, 3), np.uint8)] * (12 - len(pick))
            if c_id == 0:
                tiles.append(np.full((48, 12, 3), 255, np.uint8))
        row = cv2.hconcat(tiles)
        label = np.zeros((48, 130, 3), np.uint8)
        cv2.putText(label, vid[:11], (2, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 255, 255), 1, cv2.LINE_AA)
        rows.append(cv2.hconcat([label, row]))

    cv2.imwrite(str(R / "montage.jpg"), cv2.vconcat(rows), [cv2.IMWRITE_JPEG_QUALITY, 90])
    (R / "summary.json").write_text(json.dumps(summary, indent=2))
    ag = [v["siglip_vs_hsv_agreement"] for v in summary.values()]
    print(f"videos {len(summary)} · crops/video median {int(np.median([v['crops'] for v in summary.values()]))} · "
          f"SigLIP vs HSV agreement median {np.median(ag):.1%} min {min(ag):.1%}")


if __name__ == "__main__":
    main()
