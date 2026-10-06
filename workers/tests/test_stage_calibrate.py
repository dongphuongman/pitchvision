"""S3 calibrate on a synthetic clip with an injected fake detector / embedder (no model downloads).

Golden-data runs: eval/run_pipeline_w3.py (needs PITCHVISION_GOLDEN_DIR + the registered models).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from cv_core.teams import TeamModel, TeamSeparationFailed
from pitchvision_schemas import PipelineConfig, Shot, ShotTimeline, ShotType
from pitchvision_worker.stages.calibrate import CalibrateOutput, NoWideShots, run_calibrate
from synth_pitch_clip import FakeDetector, FakeEmbedder, write_clip

WIDE = ShotTimeline(shots=[Shot(t0_ms=0, t1_ms=8000, type=ShotType.main_wide, conf=0.9)])


@pytest.fixture(scope="module")
def clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_clip(tmp_path_factory.mktemp("clip") / "normalized.mp4")


@pytest.fixture(scope="module")
def calibrated(clip: Path, tmp_path_factory: pytest.TempPathFactory) -> CalibrateOutput:
    return run_calibrate(clip, WIDE, tmp_path_factory.mktemp("models"), PipelineConfig(), n_frames=30,
                         detector=FakeDetector(), embedder=FakeEmbedder())


def test_calibrate_writes_portable_team_model(calibrated: CalibrateOutput) -> None:
    out = calibrated
    assert out.team_model.name == "team_model.json" and out.samples.name == "team_samples.jpg"
    assert out.samples.stat().st_size > 0
    raw = json.loads(out.team_model.read_text())
    assert raw["format"] == "pv-team-model/v1" and raw["embedder"]["name"] == "fake-colour"
    assert set(raw["samples"]) == {"0", "1"} and all(len(v) == 3 for v in raw["samples"].values())
    s = raw["samples"]["0"][0]
    assert set(s) == {"crop", "frame_idx", "box"} and len(s["box"]) == 4
    assert out.frames == 30 and out.crops >= 30 * 12
    sizes = out.stats["cluster_sizes"]
    assert abs(sizes[0] - sizes[1]) <= 0.1 * sum(sizes)
    assert out.stats["outliers"] >= 25  # the orange referee (one per frame) is nobody's teammate
    TeamModel.load(out.team_model)


def test_calibrate_samples_only_analysable_shots(clip: Path, tmp_path: Path) -> None:
    tl = ShotTimeline(shots=[Shot(t0_ms=0, t1_ms=4000, type=ShotType.close_up, conf=0.9),
                             Shot(t0_ms=4000, t1_ms=8000, type=ShotType.main_wide, conf=0.9)])
    det = FakeDetector()
    out = run_calibrate(clip, tl, tmp_path, PipelineConfig(), n_frames=20, detector=det, embedder=FakeEmbedder())
    raw = json.loads(out.team_model.read_text())
    assert all(s["frame_idx"] >= 100 for v in raw["samples"].values() for s in v)  # 4 s × 25 fps
    with pytest.raises(NoWideShots):
        run_calibrate(clip, ShotTimeline(shots=tl.shots[:1]), tmp_path, PipelineConfig(), detector=det,
                      embedder=FakeEmbedder())


def test_calibrate_team_separation_failed(tmp_path: Path) -> None:
    clip = write_clip(tmp_path / "same.mp4", 4.0, same_colour=True)
    tl = ShotTimeline(shots=[Shot(t0_ms=0, t1_ms=4000, type=ShotType.main_wide, conf=0.9)])
    with pytest.raises(TeamSeparationFailed) as ei:
        run_calibrate(clip, tl, tmp_path / "m", PipelineConfig(), n_frames=30, detector=FakeDetector(),
                      embedder=FakeEmbedder())
    assert ei.value.code == "TEAM_SEPARATION_FAILED" and ei.value.reason in ("imbalanced", "not_separable")
    assert (tmp_path / "m" / "team_samples.jpg").exists()


