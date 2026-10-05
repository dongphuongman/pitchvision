"""CPU smoke test of the keypoint fine-tune pipeline on a tiny synthetic dataset rendered from the template (~1–2 min).

    uv run --project training/keypoints python training/keypoints/smoke_test.py

Checks: synthetic broadcast views (known H) → CVAT XML / Label Studio JSON / SoccerNet-style line JSON →
converters recover the projected keypoints → ``complete-from-h`` fills the unlabelled in-frame points → YOLO-pose
dataset (flip_idx) → 2 epochs of YOLO11n-pose from the yaml (no weight download) on CPU → ONNX export →
``cv_core.pitch.PitchKeypointModel`` decodes 32 keypoints. MPS backward is unreliable on torch 2.14: CPU only.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import convert as cv
import export_onnx
from cv_core.pitch import KEYPOINT_NAMES, NUM_KEYPOINTS, FIFA_105x68, PitchKeypointModel, project, render_lines
from cv_core.runtime import Runtime

SIZE = (640, 360)
SN_NAMES = {
    "touchline_far": "Side line top", "touchline_near": "Side line bottom", "goal_line_left": "Side line left",
    "goal_line_right": "Side line right", "halfway": "Middle line", "centre_circle": "Circle central",
    "penalty_area_left_far": "Big rect. left top", "penalty_area_left_front": "Big rect. left main",
    "penalty_area_left_near": "Big rect. left bottom", "penalty_area_right_far": "Big rect. right top",
    "penalty_area_right_front": "Big rect. right main", "penalty_area_right_near": "Big rect. right bottom",
    "goal_area_left_far": "Small rect. left top", "goal_area_left_front": "Small rect. left main",
    "goal_area_left_near": "Small rect. left bottom", "goal_area_right_far": "Small rect. right top",
    "goal_area_right_front": "Small rect. right main", "goal_area_right_near": "Small rect. right bottom",
}


def camera(target: tuple[float, float], cam_x: float, focal: float) -> np.ndarray:
    w, h = SIZE
    C = np.array([cam_x, 68 + 40.0, -22.0])
    T = np.array([target[0], target[1], 0.0])
    f = (T - C) / np.linalg.norm(T - C)
    r = np.cross([0.0, 0.0, 1.0], f)
    r /= np.linalg.norm(r)
    R = np.stack([r, np.cross(f, r), f])
    K = np.array([[focal, 0, w / 2], [0, focal, h / 2], [0, 0, 1]])
    H = (K @ np.c_[R, -R @ C])[:, [0, 1, 3]]
    return H / H[2, 2]


def render(H: np.ndarray, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    w, h = SIZE
    img = np.full((h, w, 3), (70, 70, 80), np.uint8)
    edge = np.concatenate([np.linspace((-5, -5), (110, -5), 100), np.linspace((110, -5), (110, 73), 100),
                           np.linspace((110, 73), (-5, 73), 100), np.linspace((-5, 73), (-5, -5), 100)])
    cv2.fillPoly(img, [np.round(np.clip(project(H, edge), -4 * w, 5 * w)).astype(np.int32)], (40, 130, 45))
    img = render_lines(H, SIZE, thickness=2, image=img, colour=(235, 240, 240))
    noise = rng.normal(0, 5, img.shape)
    return np.clip(img + noise, 0, 255).astype(np.uint8)


def truth(H: np.ndarray) -> dict[int, tuple[float, float]]:
    w, h = SIZE
    xy = project(H, FIFA_105x68.keypoints)
    return {i: (float(x), float(y)) for i, (x, y) in enumerate(xy) if 0 <= x < w and 0 <= y < h}


def write_cvat(path: Path, items: list[tuple[str, dict[int, tuple[float, float]]]]) -> None:
    rows = ['<?xml version="1.0" encoding="utf-8"?>', "<annotations>", "<version>1.1</version>"]
    for k, (name, pts) in enumerate(items):
        rows.append(f'<image id="{k}" name="{name}" width="{SIZE[0]}" height="{SIZE[1]}">')
        rows.append('<skeleton label="pitch" source="manual" z_order="0">')
        for i, (x, y) in pts.items():
            rows.append(f'<points label="{KEYPOINT_NAMES[i]}" source="manual" outside="0" occluded="0" '
                        f'points="{x:.2f},{y:.2f}" z_order="0"/>')
        rows.append('<points label="corner_tl" outside="1" occluded="0" points="0,0" z_order="0"/>' if 0 not in pts
                    else "")
        rows += ["</skeleton>", "</image>"]
    rows.append("</annotations>")
    path.write_text("\n".join(rows))


def write_labelstudio(path: Path, items: list[tuple[str, dict[int, tuple[float, float]]]]) -> None:
    tasks = []
    for name, pts in items:
        res = [{"type": "keypointlabels", "original_width": SIZE[0], "original_height": SIZE[1],
                "value": {"x": x / SIZE[0] * 100, "y": y / SIZE[1] * 100, "keypointlabels": [str(i)]}}
               for i, (x, y) in pts.items()]
        tasks.append({"data": {"image": f"/data/upload/1/abcd1234-{name}"}, "annotations": [{"result": res}]})
    path.write_text(json.dumps(tasks))


def write_soccernet(d: Path, stem: str, H: np.ndarray) -> None:
    w, h = SIZE
    out = {}
    for ln in FIFA_105x68.lines(step_m=2.0):
        if ln.name not in SN_NAMES:
            continue
        p = project(H, ln.points)
        p = p[(p[:, 0] >= 0) & (p[:, 0] < w) & (p[:, 1] >= 0) & (p[:, 1] < h)]
        if len(p) >= 2:
            out[SN_NAMES[ln.name]] = [{"x": float(x / w), "y": float(y / h)} for x, y in p]
    (d / f"{stem}.json").write_text(json.dumps(out))


def main() -> None:
    views = [((52.5, 34), 52.5, 520), ((20, 34), 40, 560), ((85, 30), 65, 600), ((60, 40), 50, 700),
             ((12, 30), 30, 900), ((95, 36), 75, 850), ((40, 30), 45, 480), ((70, 34), 60, 650)]
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        imgs = root / "frames"
        sn = root / "sn"
        imgs.mkdir()
        sn.mkdir()
        items, Hs = [], {}
        for k, (tgt, cx, f) in enumerate(views):
            H = camera(tgt, cx, f)
            name = f"synth{k % 4}_{k * 1000:07d}.jpg"
            cv2.imwrite(str(imgs / name), render(H, k))
            cv2.imwrite(str(sn / f"{k:05d}.jpg"), render(H, k))
            Hs[name] = H
            pts = truth(H)
            keep = dict(list(pts.items())[: max(6, len(pts) - 3)])  # annotators skip some points
            items.append((name, keep))
            write_soccernet(sn, f"{k:05d}", H)

        # converters
        write_cvat(root / "cvat.xml", items[:4])
        write_labelstudio(root / "ls.json", items[4:])
        fr_cvat, fr_ls = cv.from_cvat(root / "cvat.xml"), cv.from_labelstudio(root / "ls.json")
        for fr in fr_cvat + fr_ls:
            name = fr["image"]
            want = dict(items)[name]
            got = {i: (x, y) for i, x, y, _ in fr["keypoints"]}
            assert got.keys() == want.keys(), (name, sorted(got), sorted(want))
            assert max(np.hypot(got[i][0] - want[i][0], got[i][1] - want[i][1]) for i in got) < 0.05
            assert (fr["video"], fr["t_ms"]) == cv.video_time(name)
        fr_sn = cv.from_soccernet(sn)
        errs = []
        for k, fr in enumerate(fr_sn):
            want = truth(Hs[items[k][0]])
            for i, x, y, _ in fr["keypoints"]:
                errs.append(np.hypot(x - want[i][0], y - want[i][1]))
        assert errs and max(errs) < 1.5, max(errs)
        print(f"converters ok: cvat {len(fr_cvat)}, labelstudio {len(fr_ls)}, soccernet {len(fr_sn)} frames "
              f"({len(errs)} kpts, max err {max(errs):.2f} px)")

        # completion from H recovers the skipped points
        comp, ok = cv.complete_from_h(fr_cvat[0])
        full = truth(Hs[fr_cvat[0]["image"]])
        assert ok and len(comp["keypoints"]) == len(full), (len(comp["keypoints"]), len(full))

        cv.write_labels(root / "ours.json", fr_cvat + fr_ls)
        ds = root / "yolo"
        cv.to_yolo([root / "ours.json"], [imgs], ds, val=0.25, complete=True, max_px=4.0, copy=False)
        lab = next((ds / "labels" / "train").glob("*.txt")).read_text().split()
        assert len(lab) == 5 + 3 * NUM_KEYPOINTS

        # 2 epochs on CPU from the yaml (no weight download), export, decode with cv_core
        from ultralytics import YOLO

        model = YOLO("yolo11n-pose.yaml")
        model.train(data=str(ds / "data.yaml"), epochs=2, imgsz=320, batch=4, device="cpu", workers=0, amp=False,
                    plots=False, mosaic=0.0, close_mosaic=0, project=str(root / "runs"), name="smoke", verbose=False,
                    val=True)
        last = Path(model.trainer.last)  # type: ignore[union-attr]
        tl = model.trainer.tloss  # type: ignore[union-attr]
        vals = list(tl.values()) if isinstance(tl, dict) else [tl]
        loss = np.concatenate([np.asarray(v.detach().cpu() if hasattr(v, "detach") else v, float).ravel()
                               for v in vals])
        assert last.is_file() and loss.size and bool(np.all(np.isfinite(loss))), loss
        onnx_path = export_onnx.export(last, root / "pitch-kp-smoke.onnx", [320], 17, False)
        kp_model = PitchKeypointModel(Runtime("ort_cpu", models={"kp": onnx_path}).model("kp"), conf=0.0)
        kp = kp_model([cv2.imread(str(imgs / items[0][0]))])[0]
        assert kp.xy.shape == (NUM_KEYPOINTS, 2) and kp.image_size == SIZE
        print(f"train ok (loss {np.round(loss, 3).tolist()}), onnx {onnx_path.stat().st_size} B, "
              f"decode ok (score {kp.score:.3f})")
    print("SMOKE TEST PASSED")


if __name__ == "__main__":
    main()
