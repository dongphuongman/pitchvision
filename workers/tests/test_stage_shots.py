"""S2b stage wrapper on a synthetic clip with a tiny colour-only model (no weights needed)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from cv_core.shots import COLOUR_FEATURES, GbmModel, ShotClassifier, Tree
from pitchvision_schemas import PipelineConfig, ShotType
from pitchvision_worker.stages.shots import run_shots

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _model() -> GbmModel:
    stump = Tree(feature=np.array([0, 0, 0], np.int32), threshold=np.array([0.5, 0, 0.0]),
                 left=np.array([1, -1, -1], np.int32), right=np.array([2, -1, -1], np.int32),
                 value=np.array([0.0, 3.0, -3.0]), nan_left=np.zeros(3, bool))
    return GbmModel(name="t", version="1", classes=("main_wide", "non_wide"), features=COLOUR_FEATURES,
                    baseline=np.zeros(1), trees=((stump,),), class_map={"non_wide": "other"})


def test_run_shots(tmp_path: Path) -> None:
    path = tmp_path / "normalized.mp4"
    # 4 s green pitch, then 2 s red graphic: lavfi colour sources concatenated
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x28a028:s=320x180:r=25:d=4",
                    "-f", "lavfi", "-i", "color=c=0xb03030:s=320x180:r=25:d=2", "-filter_complex",
                    "[0:v][1:v]concat=n=2:v=1[v]", "-map", "[v]", "-c:v", "libx264", "-preset", "ultrafast",
                    str(path)], check=True, timeout=60)
    tl = run_shots(path, PipelineConfig(), classifier=ShotClassifier(_model()))
    assert [(s.t0_ms, s.t1_ms, s.type) for s in tl.shots] == [(0, 4000, ShotType.main_wide),
                                                            (4000, 6000, ShotType.other)]
