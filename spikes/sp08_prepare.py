"""SP-08 — prepare a test clip for overlay sync (requestVideoFrameCallback).

Takes 60 s of a golden match, normalises to 25 fps / 1280x720 (AD S2), burns the frame index
into the top-left corner as a 16-bit barcode, and writes per-frame detections to overlay.json.
The browser page (sp08_overlay/index.html) decodes the barcode from the presented frame and
compares it with the frame index the overlay code derives from rVFC metadata / currentTime.

    python sp08_prepare.py --video caZwnRXhkAk --start 60 --seconds 60
    cd sp08_overlay && python3 serve.py 8765   # then open http://localhost:8765
"""
import argparse
import json
import subprocess
from pathlib import Path

import numpy as np
from ultralytics import YOLO

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).parent / "sp08_overlay"
FPS, W, H = 25, 1280, 720
BITS, CELL = 16, 16  # barcode: [white guard][16 bits MSB first][black guard], 16x16 px cells at (0,0)


def burn(frame: np.ndarray, idx: int) -> None:
    cells = [1] + [(idx >> (BITS - 1 - b)) & 1 for b in range(BITS)] + [0]
    for i, bit in enumerate(cells):
        frame[0:CELL, i * CELL:(i + 1) * CELL] = 255 if bit else 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", default="caZwnRXhkAk")
    ap.add_argument("--start", type=float, default=60)
    ap.add_argument("--seconds", type=float, default=60)
    a = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    src = ROOT / "data" / "golden" / "raw" / f"{a.video}.mp4"
    dec = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-ss", str(a.start), "-t", str(a.seconds), "-i", str(src),
         "-vf", f"fps={FPS},scale={W}:{H}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
        stdout=subprocess.PIPE)
    enc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(FPS),
         "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-g", "50", "-pix_fmt", "yuv420p",
         "-movflags", "+faststart", str(OUT / "clip.mp4")],
        stdin=subprocess.PIPE)
    model = YOLO(str(Path(__file__).parent / "yolo11m.pt"))  # spike only; product detector per SP-11 / G-LEGAL
    frames, idx = [], 0
    while True:
        buf = dec.stdout.read(W * H * 3)
        if len(buf) < W * H * 3:
            break
        f = np.frombuffer(buf, np.uint8).reshape(H, W, 3).copy()
        r = model.predict(f, imgsz=1280, device="mps", conf=0.35, classes=[0, 32], verbose=False)[0]
        frames.append([[*map(lambda v: round(float(v), 1), b[:4]), int(b[5])] for b in r.boxes.data.cpu().numpy()])
        burn(f, idx)
        enc.stdin.write(f.tobytes())
        idx += 1
        if idx % 250 == 0:
            print(idx, "frames")
    enc.stdin.close(); enc.wait(); dec.wait()
    meta = {"fps": FPS, "width": W, "height": H, "bits": BITS, "cell": CELL, "source": a.video,
            "start_s": a.start, "frames": frames}
    (OUT / "overlay.json").write_text(json.dumps(meta, separators=(",", ":")))
    print(f"{idx} frames → {OUT/'clip.mp4'}")


if __name__ == "__main__":
    main()
