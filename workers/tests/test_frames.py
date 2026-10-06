"""cpu.extract_frame (SCR-14 / SCR-15 stills): ffmpeg reads normalized.mp4 through a presigned URL, the JPEG is
cached under keys.frame_key — real S3 (SeaweedFS session fixture) + ffmpeg."""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import cv2
import numpy as np
import pytest
from pitchvision_schemas import keys
from pitchvision_worker.frames import extract_frame, snap_ms
from pitchvision_worker.storage import ObjectStore
from synth_pitch_clip import write_clip
from worker_testkit import S3_BUCKET, s3_test_client

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def test_snap_ms() -> None:
    assert [snap_ms(t) for t in (0, 19, 20, 1013, 1020, 39_999)] == [0, 0, 40, 1000, 1040, 40_000]


def test_extract_frame_caches_jpeg(s3_endpoint: str, tmp_path: Path) -> None:
    store = ObjectStore(s3_test_client(s3_endpoint), S3_BUCKET)
    org, vid = str(uuid.uuid4()), str(uuid.uuid4())
    assert extract_frame(store, org, vid, 1000) == {"ok": False, "reason": "no_video"}

    store.upload(keys.normalized_key(org, vid), write_clip(tmp_path / "n.mp4", 2.0))
    res = extract_frame(store, org, vid, 1013)
    assert res == {"ok": True, "key": keys.frame_key(org, vid, 1000), "t_ms": 1000}
    img = cv2.imdecode(np.frombuffer(store.get_bytes(res["key"]), np.uint8), cv2.IMREAD_COLOR)
    assert img is not None and img.shape[0] > 0
    # cached: a second call never runs ffmpeg
    assert extract_frame(store, org, vid, 1000, ffmpeg="/nonexistent/ffmpeg") == res
    assert extract_frame(store, org, vid, 60_000) == {"ok": False, "reason": "out_of_range"}
