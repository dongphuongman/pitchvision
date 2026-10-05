"""SP-02 — detector throughput benchmark.

Runs on any machine: CUDA (+ TensorRT engine), Apple MPS, CoreML, or CPU.
Decode and inference are timed separately; inference is what AD 15.1 budgets per GPU.

    python sp02_bench.py --backend torch --imgsz 1280 960 --batch 1 8
    python sp02_bench.py --backend coreml --imgsz 1280 960          # macOS
    python sp02_bench.py --backend engine --imgsz 1280 960 --batch 16  # NVIDIA, exports TensorRT FP16
"""
import argparse
import json
import platform
import socket
import statistics
import time
from pathlib import Path

import cv2
import numpy as np
import torch
from ultralytics import YOLO

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "data" / "golden"
OUT = Path(__file__).parent / "results"
FRAMES_90_MIN = 135_000  # 90 min at the 25 fps the pipeline normalises to (AD S2)
PERSON, BALL = 0, 32     # COCO classes; football-specific weights replace these in M1-CV-04


def device_name() -> str:
    if torch.cuda.is_available():
        return torch.cuda.get_device_name(0)
    if torch.backends.mps.is_available():
        return platform.processor() or "Apple Silicon (MPS)"
    return platform.processor() or "CPU"


def load_frames(video: Path, start_s: float, n: int, long_side: int = 1280) -> tuple[list[np.ndarray], float]:
    """Decode n consecutive frames and resize to the pipeline's 1280px long side. Returns frames + decode fps."""
    cap = cv2.VideoCapture(str(video))
    cap.set(cv2.CAP_PROP_POS_MSEC, start_s * 1000)
    frames, t0 = [], time.perf_counter()
    while len(frames) < n:
        ok, f = cap.read()
        if not ok:
            break
        h, w = f.shape[:2]
        scale = long_side / max(h, w)
        frames.append(cv2.resize(f, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA))
    dt = time.perf_counter() - t0
    cap.release()
    return frames, len(frames) / dt if dt else 0.0


def build_model(backend: str, weights: str, imgsz: int, batch: int) -> tuple[YOLO, str]:
    if backend == "torch":
        dev = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
        return YOLO(weights), dev
    if backend == "coreml":
        # nms=True trips a coremltools conversion bug (int cast of a non-scalar); NMS runs in Python instead
        path = YOLO(weights).export(format="coreml", imgsz=imgsz, nms=False)
        return YOLO(path, task="detect"), "cpu"  # CoreML picks ANE/GPU itself
    if backend == "engine":
        path = YOLO(weights).export(format="engine", imgsz=imgsz, half=True, batch=batch, dynamic=False)
        return YOLO(path, task="detect"), "cuda"
    if backend == "onnx":
        path = YOLO(weights).export(format="onnx", imgsz=imgsz, half=False, batch=batch)
        return YOLO(path, task="detect"), "cuda" if torch.cuda.is_available() else "cpu"
    raise ValueError(backend)


def run(model: YOLO, device: str, frames: list[np.ndarray], imgsz: int, batch: int, half: bool) -> tuple[float, list[int], list[int]]:
    persons, balls = [], []
    chunks = [frames[i:i + batch] for i in range(0, len(frames), batch)]
    model.predict(chunks[0], imgsz=imgsz, device=device, half=half, verbose=False, conf=0.35)  # warm-up
    if device == "mps":
        torch.mps.synchronize()
    t0 = time.perf_counter()
    for c in chunks:
        for r in model.predict(c, imgsz=imgsz, device=device, half=half, verbose=False, conf=0.35, classes=[PERSON, BALL]):
            cls = r.boxes.cls.int().tolist()
            persons.append(cls.count(PERSON))
            balls.append(cls.count(BALL))
    if device == "mps":
        torch.mps.synchronize()
    elif device == "cuda":
        torch.cuda.synchronize()
    dt = time.perf_counter() - t0
    return len(frames) / dt, persons, balls


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="torch", choices=["torch", "coreml", "engine", "onnx"])
    ap.add_argument("--weights", default="yolo11m.pt")
    ap.add_argument("--imgsz", type=int, nargs="+", default=[1280, 960])
    ap.add_argument("--batch", type=int, nargs="+", default=[1])
    ap.add_argument("--videos", type=int, default=6, help="how many golden videos to sample (0 = all)")
    ap.add_argument("--frames", type=int, default=128, help="consecutive frames per video")
    args = ap.parse_args()

    manifest = [m for m in json.loads((GOLDEN / "manifest.json").read_text()) if m["downloaded"]]
    if args.videos:
        manifest = manifest[:: max(1, len(manifest) // args.videos)][: args.videos]

    frames, decode_fps = [], []
    for m in manifest:
        f, fps = load_frames(GOLDEN / m["file"], 60, args.frames)
        frames += f
        decode_fps.append(fps)
    print(f"{len(frames)} frames from {len(manifest)} videos · decode+resize {statistics.mean(decode_fps):.0f} fps (CPU)")

    results = []
    for imgsz in args.imgsz:
        for batch in (args.batch if args.backend in ("torch", "engine", "onnx") else [1]):
            model, dev = build_model(args.backend, args.weights, imgsz, batch)
            half = dev == "cuda"
            fps, persons, balls = run(model, dev, frames, imgsz, batch, half)
            res = {
                "backend": args.backend, "device": dev, "hw": device_name(), "weights": args.weights,
                "imgsz": imgsz, "batch": batch, "fps": round(fps, 1),
                "minutes_90min_stride1": round(FRAMES_90_MIN / fps / 60, 1),
                "minutes_90min_stride2": round(FRAMES_90_MIN / 2 / fps / 60, 1),
                "persons_median": statistics.median(persons), "ball_detect_rate": round(sum(b > 0 for b in balls) / len(balls), 3),
                "decode_fps_cpu": round(statistics.mean(decode_fps), 1), "frames": len(frames),
            }
            results.append(res)
            print(f"{args.backend:6} {dev:4} imgsz={imgsz:<5} batch={batch:<3} {fps:6.1f} fps → 90' in "
                  f"{res['minutes_90min_stride1']:5.1f} min (stride 2: {res['minutes_90min_stride2']:4.1f}) · "
                  f"persons/frame {res['persons_median']} · ball found {res['ball_detect_rate']:.0%}")

    OUT.mkdir(exist_ok=True)
    out = OUT / f"sp02_{socket.gethostname().split('.')[0]}_{args.backend}.json"
    out.write_text(json.dumps(results, indent=2))
    print("saved", out)


if __name__ == "__main__":
    main()
