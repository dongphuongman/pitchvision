"""Still frames of normalized.mp4 on demand (SCR-14 manual teams, SCR-15 manual calibration).

Celery ``cpu.extract_frame`` (queue ``cpu``), kwargs ``{org_id, video_id, t_ms}`` — called synchronously by the API
(``GET /api/v1/results/{video}/calibration/frame``) with a short timeout. ``t_ms`` is snapped to the 25 fps frame
grid (``snap_ms``: the same formula as the API) and the JPEG is cached at ``keys.frame_key`` (``media/…/frames/``):
a second request for the same frame never reaches the worker. ffmpeg reads the object through a short-lived
presigned URL (input seeking: only the GOP around ``t_ms`` is fetched, never the whole file).

Returns ``{"ok": true, "key", "t_ms"}`` or ``{"ok": false, "reason": "no_video" | "out_of_range" | "ffmpeg"}``.
"""
from __future__ import annotations

import logging
import math
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from pitchvision_schemas import keys

from pitchvision_worker.celery_app import QUEUE_CPU, app
from pitchvision_worker.config import WorkerConfig
from pitchvision_worker.storage import ObjectStore

log = logging.getLogger(__name__)

TASK_EXTRACT_FRAME = "cpu.extract_frame"
FPS = 25
FFMPEG_TIMEOUT_S = 30.0
PRESIGN_TTL_S = 300
JPEG_Q = 3  # ffmpeg -q:v (2 best … 31 worst)


def snap_ms(t_ms: int, fps: int = FPS) -> int:
    """``t_ms`` → start time of the nearest frame of the normalized (constant ``fps``) timeline."""
    return round(math.floor(t_ms * fps / 1000 + 0.5) * 1000 / fps)


def extract_frame(store: ObjectStore, org_id: str, video_id: str, t_ms: int, *, ffmpeg: str | None = None,
                  timeout_s: float = FFMPEG_TIMEOUT_S) -> dict[str, Any]:
    t = snap_ms(max(0, int(t_ms)))
    key = keys.frame_key(org_id, video_id, t)
    if store.exists(key):
        return {"ok": True, "key": key, "t_ms": t}
    src = keys.normalized_key(org_id, video_id)
    if not store.exists(src):
        return {"ok": False, "reason": "no_video"}
    url = store.client.generate_presigned_url("get_object", Params={"Bucket": store.bucket, "Key": src},
                                              ExpiresIn=PRESIGN_TTL_S)  # fmt: skip
    exe = ffmpeg or shutil.which("ffmpeg") or "ffmpeg"
    with tempfile.TemporaryDirectory(prefix="pv-frame-") as tmp:
        out = Path(tmp) / "frame.jpg"
        cmd = [exe, "-nostdin", "-hide_banner", "-loglevel", "error", "-protocol_whitelist", "file,http,https,tcp,tls",
               "-ss", f"{t / 1000:.3f}", "-i", url, "-frames:v", "1", "-pix_fmt", "yuvj420p", "-q:v", str(JPEG_Q),
               "-y", str(out)]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout_s, check=False)
        except subprocess.TimeoutExpired:
            log.warning("extract_frame %s @%d ms: ffmpeg timed out", video_id, t)
            return {"ok": False, "reason": "ffmpeg"}
        if proc.returncode != 0 and b"Nothing was written" in proc.stderr:  # seek past the end
            return {"ok": False, "reason": "out_of_range"}
        if proc.returncode != 0:
            log.warning("extract_frame %s @%d ms: ffmpeg failed: %s", video_id, t, proc.stderr[-500:])
            return {"ok": False, "reason": "ffmpeg"}
        if not out.exists() or out.stat().st_size == 0:
            return {"ok": False, "reason": "out_of_range"}
        store.upload(key, out)
    return {"ok": True, "key": key, "t_ms": t}


@app.task(name=TASK_EXTRACT_FRAME, queue=QUEUE_CPU, acks_late=False)
def extract_frame_task(org_id: str, video_id: str, t_ms: int) -> dict[str, Any]:
    return extract_frame(ObjectStore.from_config(WorkerConfig.from_env()), org_id, video_id, t_ms)
