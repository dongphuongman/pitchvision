"""PnLCalib on PitchVision frames (research-only weights, GPL code — internal evaluation, never shipped).

For every main_wide shot midpoint of a normalized.mp4: PnLCalib keypoints + lines → camera → ground-plane
homography in OUR template frame (cv_core.pitch, origin top-left corner flag, y down the image) → cv_core h_quality
(line alignment, ≥ 0.5 = usable). Writes overlays + summary.json under out/.

  .venv/bin/python eval_frames.py normalized.mp4 shot_mids.txt [--device mps|cpu] [--no-refine]
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import torch
import torchvision.transforms as T
import torchvision.transforms.functional as F
import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE / "repo"
sys.path[:0] = [str(REPO), str(HERE.parents[1] / "packages" / "cv-core")]

from cv_core.pitch.lines import h_quality, render_lines  # noqa: E402
from model.cls_hrnet import get_cls_net  # noqa: E402
from model.cls_hrnet_l import get_cls_net as get_cls_net_l  # noqa: E402
from utils.utils_calib import FramebyFrameCalib  # noqa: E402
from utils.utils_heatmap import (complete_keypoints, coords_to_dict,  # noqa: E402
                                 get_keypoints_from_heatmap_batch_maxpool,
                                 get_keypoints_from_heatmap_batch_maxpool_l)

KP_T, LINE_T = 0.3434, 0.7867
SHIFT = np.array([[1, 0, -52.5], [0, 1, -34.0], [0, 0, 1]])  # ours (corner origin) → PnLCalib (centre origin)


def load(device: str) -> tuple[torch.nn.Module, torch.nn.Module]:
    os.chdir(REPO)
    cfg, cfg_l = (yaml.safe_load(open(f"config/{n}.yaml")) for n in ("hrnetv2_w48", "hrnetv2_w48_l"))
    m, ml = get_cls_net(cfg), get_cls_net_l(cfg_l)
    m.load_state_dict(torch.load(HERE / "weights" / "SV_kp", map_location="cpu"))
    ml.load_state_dict(torch.load(HERE / "weights" / "SV_lines", map_location="cpu"))
    return m.to(device).eval(), ml.to(device).eval()


def projection(params: dict) -> np.ndarray:
    c = params["cam_params"]
    It = np.eye(4)[:-1]
    It[:, -1] = -np.array(c["position_meters"])
    K = np.array([[c["x_focal_length"], 0, c["principal_point"][0]], [0, c["y_focal_length"], c["principal_point"][1]],
                  [0, 0, 1]])
    return K @ (np.array(c["rotation_matrix"]) @ It)


def to_ours(P: np.ndarray) -> np.ndarray:
    """Ground-plane H (our pitch metres → image px), with x growing left→right and y growing down the image."""
    return normalize(P[:, [0, 1, 3]] @ SHIFT)


def normalize(H: np.ndarray) -> np.ndarray:
    """H (our pitch metres → image) with x growing left→right and y growing down the image."""

    def img(x: float, y: float) -> np.ndarray:
        p = H @ np.array([x, y, 1.0])
        return p[:2] / p[2]

    if img(52.5, 0)[1] > img(52.5, 68)[1]:  # far touchline must be higher in the image
        H = H @ np.array([[1, 0, 0], [0, -1, 68], [0, 0, 1]])
    if img(0, 34)[0] > img(105, 34)[0]:
        H = H @ np.array([[-1, 0, 105], [0, 1, 0], [0, 0, 1]])
    return H / H[2, 2]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("mids")
    ap.add_argument("--device", default="mps" if torch.backends.mps.is_available() else "cpu")
    ap.add_argument("--no-refine", action="store_true")
    ap.add_argument("--out", default=str(HERE / "out"))
    a = ap.parse_args()
    video, out = Path(a.video).resolve(), Path(a.out)
    out.mkdir(exist_ok=True)
    mids = [int(x) for x in Path(a.mids).read_text().strip().split(",") if x]
    model, model_l = load(a.device)
    cap = cv2.VideoCapture(str(video))
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cam = FramebyFrameCalib(iwidth=w, iheight=h, denormalize=True)
    resize = T.Resize((540, 960))
    rows = []
    for ms in mids:
        cap.set(cv2.CAP_PROP_POS_MSEC, ms)
        ok, frame = cap.read()
        if not ok:
            continue
        t0 = time.perf_counter()
        x = resize(F.to_tensor(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).float().unsqueeze(0)).to(a.device)
        with torch.no_grad():
            hm, hml = model(x), model_l(x)
        t_net = time.perf_counter() - t0
        kp = coords_to_dict(get_keypoints_from_heatmap_batch_maxpool(hm[:, :-1].cpu()), threshold=KP_T)
        ln = coords_to_dict(get_keypoints_from_heatmap_batch_maxpool_l(hml[:, :-1].cpu()), threshold=LINE_T)
        kp, ln = complete_keypoints(kp[0], ln[0], w=960, h=540, normalize=True)
        cam.update(kp, ln)
        params = cam.heuristic_voting(refine_lines=not a.no_refine)
        t_all = time.perf_counter() - t0
        q, H = 0.0, None
        if params is not None:
            H = to_ours(projection(params))
            q = h_quality(frame, H)
            over = render_lines(H, (w, h), thickness=2, image=frame)
            cv2.imwrite(str(out / f"{ms:07d}_q{q:.2f}.jpg"), over)
        rows.append({"ms": ms, "kp": len(kp), "lines": len(ln), "ok": params is not None, "quality": round(q, 3),
                     "net_s": round(t_net, 2), "total_s": round(t_all, 2),
                     "H": None if H is None else H.round(6).tolist()})
        print(f"{ms/1000:7.1f}s kp={len(kp):2d} lines={len(ln):2d} q={q:.2f} net={t_net:.2f}s total={t_all:.2f}s")
    qs = [r["quality"] for r in rows]
    summary = {"frames": len(rows), "usable_q05": sum(q >= 0.5 for q in qs), "median_q": float(np.median(qs)),
               "median_total_s": float(np.median([r["total_s"] for r in rows])), "device": a.device,
               "refine": not a.no_refine, "rows": rows}
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    print({k: v for k, v in summary.items() if k != "rows"})


if __name__ == "__main__":
    main()
