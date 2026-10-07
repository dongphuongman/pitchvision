"""label_tool.py: fit overlay, saving pv-pitch-keypoints/v1 rows, skipping frames (workspace env:
``uv run pytest training/keypoints/test_label_tool.py``)."""
import json
import sys
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import label_tool as lt  # noqa: E402

from cv_core.pitch import FIFA_105x68, project  # noqa: E402


def _post(base: str, path: str, body: dict) -> dict:
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST")
    return json.loads(urllib.request.urlopen(req).read())


def test_label_tool_roundtrip(tmp_path: Path) -> None:
    frames, out = tmp_path / "frames", tmp_path / "kp" / "ours.json"
    frames.mkdir()
    for n in ("match1_0001000.jpg", "match1_0002000.jpg"):
        cv2.imwrite(str(frames / n), np.zeros((720, 1280, 3), np.uint8))
    store = lt.Store(frames, out)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), lt.handler(store))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        state = json.loads(urllib.request.urlopen(base + "/api/state").read())
        assert state["images"] == ["match1_0001000.jpg", "match1_0002000.jpg"] and len(state["template"]["names"]) == 32
        H = np.array([[10.0, 2.0, 100.0], [0.0, 8.0, 80.0], [0.0, 0.002, 1.0]])  # pitch m → image px
        idx = [0, 9, 13, 16, 24, 29]
        img = project(H, FIFA_105x68.keypoints[idx].astype(float))
        pts = [[k, float(x), float(y)] for k, (x, y) in zip(idx, img, strict=True)]
        assert _post(base, "/api/fit", {"points": pts[:3]}) == {"ok": False}
        fit = _post(base, "/api/fit", {"points": pts})
        assert fit["ok"] and fit["rmse"] < 0.5 and len(fit["keypoints"]) == 32
        assert _post(base, "/api/labels/match1_0001000.jpg", {"keypoints": [[*p, 2] for p in pts]})["saved"]
        assert _post(base, "/api/labels/match1_0002000.jpg", {"skip": True})["saved"]
        data = json.loads(out.read_text())
        assert data["format"] == "pv-pitch-keypoints/v1" and data["skipped"] == ["match1_0002000.jpg"]
        row = data["frames"][0]
        assert (row["video"], row["t_ms"], row["width"], row["height"]) == ("match1", 1000, 1280, 720)
        assert [k[0] for k in row["keypoints"]] == idx and row["keypoints"][0][3] == 2
        # restart: labels come back
        assert set(lt.Store(frames, out).rows) == {"match1_0001000.jpg"}
    finally:
        srv.shutdown()
