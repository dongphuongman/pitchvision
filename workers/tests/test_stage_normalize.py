"""S2 stage wrapper: config → cv_core.normalize, manifest fields shape."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from pitchvision_schemas import PipelineConfig
from pitchvision_worker.stages.normalize import NORMALIZED_NAME, run_normalize

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def test_run_normalize_writes_normalized_mp4(tmp_path: Path) -> None:
    src = tmp_path / "original.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=50:duration=3",
                    "-c:v", "libx264", "-preset", "ultrafast", str(src)], check=True, timeout=60)
    out = run_normalize(src, tmp_path / "out", PipelineConfig())
    assert out.path == tmp_path / "out" / NORMALIZED_NAME and out.path.is_file()
    assert out.manifest == {"fps": 25, "width": 1280, "height": 720, "duration_ms": 3000, "frame_count": 75}
    assert out.source["fps"] == 50 and out.source["width"] == 1920
