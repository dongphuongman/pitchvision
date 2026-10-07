"""Keypoint heatmaps only (no PnLCalib calibration code): per-channel peak → world table → RANSAC homography → our
h_quality. What the PitchVision runtime would do with the exported ONNX."""
import json, sys, time
from pathlib import Path
import cv2, numpy as np, torch
sys.path[:0] = [str(Path(__file__).parent)]
import eval_frames as ef  # loads repo paths + models
from utils.utils_calib import keypoint_world_coords_2D as WORLD_C  # centred coords (x-52.5, y-34)

WORLD = np.array(WORLD_C, float) + [52.5, 34.0]  # → corner origin
GOALPOST_TOP = {12, 15, 16, 19}  # 1-based, 2.44 m above ground

def decode(hm: np.ndarray, thr: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    c, h, w = hm.shape
    flat = hm.reshape(c, -1)
    idx = flat.argmax(1)
    score = flat.max(1)
    ys, xs = np.divmod(idx, w)
    keep = (score > thr) & (xs > 0) & (ys > 0) & (xs < w - 1) & (ys < h - 1)
    return np.flatnonzero(keep), np.stack([xs, ys], 1)[keep] * 2.0, score[keep]

def main() -> None:
    video, mids, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).read_text().strip().split(","), Path(__file__).parent / "out_kp"
    out.mkdir(exist_ok=True)
    model, _ = ef.load("mps")
    cap = cv2.VideoCapture(str(video))
    W, Hh = int(cap.get(3)), int(cap.get(4))
    rows = []
    for ms in map(int, mids):
        cap.set(cv2.CAP_PROP_POS_MSEC, ms)
        ok, frame = cap.read()
        t0 = time.perf_counter()
        x = ef.T.Resize((540, 960))(ef.F.to_tensor(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).float().unsqueeze(0)).to("mps")
        with torch.no_grad():
            hm = model(x)[0, :-1].cpu().numpy()
        ch, pts, sc = decode(hm, ef.KP_T)
        sel = [i for i, c in enumerate(ch) if c + 1 not in GOALPOST_TOP]
        ch, pts = ch[sel], pts[sel] * [W / 960, Hh / 540]
        q, H = 0.0, None
        if len(ch) >= 4:
            Hi2p, inl = cv2.findHomography(pts.astype(np.float32), WORLD[ch].astype(np.float32), cv2.RANSAC, 1.5)
            if Hi2p is not None and inl.sum() >= 4:
                raw = np.linalg.inv(Hi2p)
                def img(x, y, H=raw):
                    v = H @ np.array([x, y, 1.0]); return v[:2] / v[2]
                print("FLIP", ms, "y" if img(52.5, 0)[1] > img(52.5, 68)[1] else "-", "x" if img(0, 34)[0] > img(105, 34)[0] else "-")
                H = ef.normalize(raw)
                q = ef.h_quality(frame, H)
                cv2.imwrite(str(out / f"{ms:07d}_q{q:.2f}.jpg"), ef.render_lines(H, (W, Hh), thickness=2, image=frame))
        rows.append({"ms": ms, "kp": int(len(ch)), "quality": round(q, 3), "s": round(time.perf_counter() - t0, 2)})
        print(rows[-1])
    qs = [r["quality"] for r in rows]
    print({"usable_q05": sum(q >= 0.5 for q in qs), "frames": len(qs), "median_q": float(np.median(qs))})

main()
