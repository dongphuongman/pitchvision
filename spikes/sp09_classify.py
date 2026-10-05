"""SP-09 step 2 — wide-shot classifier baseline on the labelled golden frames.

(a) hand rule on green ratio + person size, (b) gradient boosting on the same features,
both scored with leave-videos-out cross-validation so no video is seen in training and test.
"""
import json
from pathlib import Path

import cv2
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.model_selection import GroupKFold
from ultralytics import YOLO

R = Path(__file__).parent / "results" / "sp09"


def colour_features(f: np.ndarray) -> list[float]:
    hsv = cv2.cvtColor(f, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, (30, 40, 40), (90, 255, 255)) > 0
    lower = green[green.shape[0] // 3:]
    white = cv2.inRange(hsv, (0, 0, 170), (180, 60, 255)) > 0
    on_pitch = cv2.dilate(green.astype(np.uint8), np.ones((9, 9), np.uint8)) > 0
    lines = (white & on_pitch).mean()
    edges = cv2.Canny(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), 80, 160).mean() / 255
    return [green.mean(), lower.mean(), lines, edges]


def main() -> None:
    index = json.loads((R / "index.json").read_text())
    labels = json.loads((R / "labels_nonwide.json").read_text())
    model = YOLO("yolo11m.pt")
    X, y, groups = [], [], []
    for it in index:
        f = cv2.imread(str(R / "frames" / it["file"]))
        H = f.shape[0]
        r = model.predict(f, imgsz=640, conf=0.3, classes=[0], device="mps", verbose=False)[0]
        hs = (r.boxes.xywh[:, 3].cpu().numpy() / H) if len(r.boxes) else np.array([0.0])
        X.append(colour_features(f) + [len(r.boxes), hs.max(), float(np.median(hs))])
        y.append(0 if it["k"] in labels[it["video"]] else 1)
        groups.append(it["video"])
    X, y, groups = np.array(X), np.array(y), np.array(groups)
    np.savez(R / "features.npz", X=X, y=y, groups=groups)

    # (a) hand rule: lots of grass, several players, nobody large in frame
    rule = ((X[:, 1] > 0.55) & (X[:, 4] >= 4) & (X[:, 5] < 0.22)).astype(int)

    # (b) gradient boosting, leave-videos-out
    pred = np.zeros_like(y)
    for tr, te in GroupKFold(n_splits=5).split(X, y, groups):
        clf = HistGradientBoostingClassifier(max_depth=3, max_iter=200, learning_rate=0.08)
        clf.fit(X[tr], y[tr])
        pred[te] = clf.predict(X[te])

    def report(name: str, p: np.ndarray) -> dict:
        acc = (p == y).mean()
        prec = (p & y).sum() / max(p.sum(), 1)            # of frames called wide, how many truly wide
        rec = (p & y).sum() / max(y.sum(), 1)             # of truly wide frames, how many kept
        per_video = {g: round(float((p[groups == g] == y[groups == g]).mean()), 3) for g in sorted(set(groups))}
        worst = sorted(per_video.items(), key=lambda kv: kv[1])[:3]
        print(f"{name:22} acc {acc:.1%} · wide precision {prec:.1%} · wide recall {rec:.1%} · worst {worst}")
        return {"accuracy": round(float(acc), 4), "wide_precision": round(float(prec), 4),
                "wide_recall": round(float(rec), 4), "per_video": per_video}

    cov = {g: round(float(y[groups == g].mean()), 3) for g in sorted(set(groups))}
    print(f"{len(y)} frames · wide share (ground truth) {y.mean():.1%} · per-video coverage "
          f"min {min(cov.values()):.0%} / median {np.median(list(cov.values())):.0%} / max {max(cov.values()):.0%}")
    out = {"frames": int(len(y)), "wide_share": round(float(y.mean()), 4), "coverage_by_video": cov,
           "rule": report("(a) rule", rule), "gbm_leave_videos_out": report("(b) GBM leave-videos-out", pred)}
    (R / "sp09_results.json").write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
