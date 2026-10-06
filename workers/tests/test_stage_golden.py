"""Optional golden smoke test: real models on 40 s of a golden match (skipped unless PITCHVISION_GOLDEN_DIR is set
and the registered models are in PITCHVISION_MODEL_DIR). Full numbers: eval/run_pipeline_w3.py."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from cv_core.io.parquet import read_detections
from cv_core.registry import ModelRegistry
from pitchvision_schemas import PipelineConfig
from pitchvision_worker.stages.calibrate import run_calibrate
from pitchvision_worker.stages.infer import run_infer
from pitchvision_worker.stages.normalize import run_normalize
from pitchvision_worker.stages.segments import plan_segments
from pitchvision_worker.stages.shots import run_shots

GOLDEN = os.environ.get("PITCHVISION_GOLDEN_DIR")
VIDEO = "TyCCa3tyhZw"


def _models_ready() -> bool:
    try:
        reg = ModelRegistry.load()
        for name in ("dfine-m-obj2coco-640", "siglip-base-p16-224", "shots-gbm-det"):
            reg.resolve(name)
        return True
    except (FileNotFoundError, KeyError):
        return False


pytestmark = [
    pytest.mark.skipif(not GOLDEN, reason="PITCHVISION_GOLDEN_DIR not set"),
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed"),
]


def test_golden_s2_to_s4(tmp_path: Path) -> None:
    src = Path(GOLDEN or "") / "raw" / f"{VIDEO}.mp4"
    if not src.exists() or not _models_ready():
        pytest.skip("golden video or registered models missing")
    cut = tmp_path / "original.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "300", "-i", str(src), "-t", "40", "-an", "-c", "copy",
                    str(cut)], check=True, timeout=120)
    cfg = PipelineConfig()
    norm = run_normalize(cut, tmp_path, cfg).path
    tl = run_shots(norm, cfg)
    segs = plan_segments(tl, tl.shots[-1].t1_ms, cfg)
    assert segs, "no main_wide footage in the test window"
    cal = run_calibrate(norm, tl, tmp_path / "models", cfg, n_frames=24)
    assert cal.crops >= 40 and cal.team_model.exists()
    seg = max(segs, key=lambda s: s.t1_ms - s.t0_ms)
    out = run_infer(norm, seg, cal.team_model, tmp_path / "seg", cfg)
    t = read_detections(out.paths["detections"])
    teams = set(t.column("team").to_pylist())
    assert {0, 1} <= teams and out.stats["tracks"] >= 10
