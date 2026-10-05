"""SP-09 step 1 — sample one frame every STEP seconds from each golden segment and build numbered grids for labelling."""
import json
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "data" / "golden"
OUT = Path(__file__).parent / "results" / "sp09"
STEP = 20          # seconds
COLS, TW = 9, 200  # grid columns, tile width


def main() -> None:
    (OUT / "frames").mkdir(parents=True, exist_ok=True)
    (OUT / "grids").mkdir(parents=True, exist_ok=True)
    index = []
    for m in json.loads((GOLDEN / "manifest.json").read_text()):
        if not m["downloaded"]:
            continue
        cap = cv2.VideoCapture(str(GOLDEN / m["file"]))
        tiles = []
        for k, t in enumerate(range(5, int(m["duration_s"]) - 1, STEP)):
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, f = cap.read()
            if not ok:
                continue
            f = cv2.resize(f, (640, 360), interpolation=cv2.INTER_AREA)
            name = f"{m['id']}_{t:04d}.jpg"
            cv2.imwrite(str(OUT / "frames" / name), f)
            index.append({"video": m["id"], "t": t, "k": k, "file": name})
            tile = cv2.resize(f, (TW, TW * 9 // 16))
            cv2.rectangle(tile, (0, 0), (34, 20), (0, 0, 0), -1)
            cv2.putText(tile, str(k), (3, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1, cv2.LINE_AA)
            tiles.append(tile)
        cap.release()
        while len(tiles) % COLS:
            tiles.append(np.zeros_like(tiles[0]))
        rows = [cv2.hconcat(tiles[i:i + COLS]) for i in range(0, len(tiles), COLS)]
        cv2.imwrite(str(OUT / "grids" / f"{m['id']}.jpg"), cv2.vconcat(rows), [cv2.IMWRITE_JPEG_QUALITY, 85])
    (OUT / "index.json").write_text(json.dumps(index, indent=1))
    print(len(index), "frames,", len({i["video"] for i in index}), "grids")


if __name__ == "__main__":
    main()
