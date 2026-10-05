"""SP-03 — pitch keypoints → RANSAC homography on wide-angle frames of the golden set.

Success per frame = ≥4 confident keypoints, homography found, ≥50% inliers, median reprojection error < 1 m.
Plausibility = share of detected players whose projected feet land inside the pitch (±3 m).
"""
import json
from pathlib import Path

import cv2
import numpy as np
from sports.configs.soccer import SoccerPitchConfiguration
from ultralytics import YOLO

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "data" / "golden"
R = Path(__file__).parent / "results" / "sp03"
NONWIDE = json.loads((Path(__file__).parent / "results" / "sp09" / "labels_nonwide.json").read_text())
CFG = SoccerPitchConfiguration()
PITCH = np.array(CFG.vertices, dtype=np.float32)  # cm, 12000 × 7000
DEV = "mps"


def draw_pitch(frame: np.ndarray, H_img2pitch: np.ndarray) -> np.ndarray:
    inv = np.linalg.inv(H_img2pitch)
    out = frame.copy()
    for a, b in CFG.edges:
        p = cv2.perspectiveTransform(PITCH[[a - 1, b - 1]].reshape(-1, 1, 2), inv).reshape(-1, 2)
        cv2.line(out, tuple(p[0].astype(int)), tuple(p[1].astype(int)), (255, 0, 255), 3, cv2.LINE_AA)
    return out


def line_alignment(frame: np.ndarray, H_img2pitch: np.ndarray) -> float:
    """Share of projected pitch-line pixels (on grass) that fall within 4 px of a real white line pixel."""
    inv = np.linalg.inv(H_img2pitch)
    h, w = frame.shape[:2]
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    grass = cv2.dilate(cv2.inRange(hsv, (30, 40, 40), (90, 255, 255)), np.ones((15, 15), np.uint8)) > 0
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    tophat = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, np.ones((15, 15), np.uint8))
    white = cv2.dilate(((tophat > 25) & grass).astype(np.uint8), np.ones((9, 9), np.uint8)) > 0
    line = np.zeros((h, w), np.uint8)
    for a, b in CFG.edges:
        seg = np.linspace(PITCH[a - 1], PITCH[b - 1], 60, dtype=np.float32)
        p = cv2.perspectiveTransform(seg.reshape(-1, 1, 2), inv).reshape(-1, 2)
        for q0, q1 in zip(p[:-1], p[1:]):
            if np.all(np.isfinite([q0, q1])) and abs(q0).max() < 1e5 and abs(q1).max() < 1e5:
                cv2.line(line, tuple(q0.astype(int)), tuple(q1.astype(int)), 1, 2)
    on = (line > 0) & grass
    return float((on & white).sum() / on.sum()) if on.sum() > 200 else 0.0


def main() -> None:
    R.mkdir(parents=True, exist_ok=True)
    kp_model = YOLO(str(Path(__file__).parent / "weights" / "football-pitch-detection.pt"))
    det = YOLO("yolo11m.pt")
    summary, samples = {}, []
    for m in json.loads((GOLDEN / "manifest.json").read_text()):
        if not m["downloaded"]:
            continue
        vid = m["id"]
        cap = cv2.VideoCapture(str(GOLDEN / m["file"]))
        ok_n, total, errs, inside, n_kp, aligns = 0, 0, [], [], [], []
        for j, k in enumerate([k for k in range(45) if k not in NONWIDE[vid]][:30]):
            cap.set(cv2.CAP_PROP_POS_MSEC, (5 + 20 * k) * 1000)
            ok, f = cap.read()
            if not ok:
                continue
            total += 1
            r = kp_model.predict(f, imgsz=640, conf=0.3, device=DEV, verbose=False)[0]
            if r.keypoints is None or len(r.keypoints) == 0:
                n_kp.append(0)
                continue
            xy = r.keypoints.xy[0].cpu().numpy()
            cf = r.keypoints.conf[0].cpu().numpy()
            hh, ww = f.shape[:2]
            margin = 0.03 * ww  # the model hallucinates confident points at the image border
            sel = (cf > 0.5) & (xy[:, 0] > margin) & (xy[:, 0] < ww - margin) & (xy[:, 1] > margin) & (xy[:, 1] < hh - margin)
            n_kp.append(int(sel.sum()))
            if sel.sum() < 6:
                continue
            H, mask = cv2.findHomography(xy[sel], PITCH[sel], cv2.RANSAC, 50.0)  # 0.5 m RANSAC threshold
            if H is None:
                continue
            proj = cv2.perspectiveTransform(xy[sel].reshape(-1, 1, 2), H).reshape(-1, 2)
            err = np.linalg.norm(proj - PITCH[sel], axis=1)
            inl = mask.ravel().astype(bool)
            med_err = float(np.median(err[inl])) / 100 if inl.any() else 99
            align = line_alignment(f, H)
            aligns.append(align)
            if inl.sum() < 6 or med_err >= 1.0 or align < 0.6:
                continue
            ok_n += 1
            errs.append(med_err)
            p = det.predict(f, imgsz=1280, conf=0.4, classes=[0], device=DEV, verbose=False)[0].boxes.xyxy.cpu().numpy()
            if len(p):
                feet = np.stack([(p[:, 0] + p[:, 2]) / 2, p[:, 3]], 1).astype(np.float32)
                fp = cv2.perspectiveTransform(feet.reshape(-1, 1, 2), H).reshape(-1, 2) / 100
                inside.append(float(((fp[:, 0] > -3) & (fp[:, 0] < 123) & (fp[:, 1] > -3) & (fp[:, 1] < 73)).mean()))
            if j in (3, 17) and len(samples) < 16:
                img = cv2.resize(draw_pitch(f, H), (480, 270))
                cv2.putText(img, f'align {align:.2f}', (8, 260), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
                samples.append(img)
        cap.release()
        summary[vid] = {"category": m["category"], "frames": total, "success": round(ok_n / max(total, 1), 3),
                        "median_kp": int(np.median(n_kp)) if n_kp else 0,
                        "median_alignment": round(float(np.median(aligns)), 2) if aligns else None,
                        "median_reproj_m": round(float(np.median(errs)), 2) if errs else None,
                        "feet_inside_pitch": round(float(np.mean(inside)), 3) if inside else None}
        print(vid, summary[vid])

    rows = [cv2.hconcat(samples[i:i + 4]) for i in range(0, len(samples) - len(samples) % 4, 4)]
    if rows:
        cv2.imwrite(str(R / "overlay.jpg"), cv2.vconcat(rows), [cv2.IMWRITE_JPEG_QUALITY, 85])
    (R / "summary.json").write_text(json.dumps(summary, indent=2))
    s = [v["success"] for v in summary.values()]
    allf = sum(v["frames"] for v in summary.values())
    okf = sum(v["success"] * v["frames"] for v in summary.values())
    print(f"overall {okf / allf:.1%} of {allf} wide frames · per-video median {np.median(s):.0%} min {min(s):.0%}")
    for cat in ("men", "women", "youth"):
        c = [v for v in summary.values() if v["category"] == cat]
        print(f"  {cat:6} {sum(v['success'] * v['frames'] for v in c) / sum(v['frames'] for v in c):.1%}")


if __name__ == "__main__":
    main()
