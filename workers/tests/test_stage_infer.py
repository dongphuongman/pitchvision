"""S4 infer on a synthetic clip with an injected fake detector / embedder (no model downloads).

Golden-data runs: eval/run_pipeline_w3.py (needs PITCHVISION_GOLDEN_DIR + the registered models).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from cv_core.io.parquet import CLS_BALL, CLS_PLAYER, NO_TRACK, detections_metadata, read_detections
from cv_core.teams import TeamModel
from pitchvision_schemas import PipelineConfig, Segment, Shot, ShotTimeline, ShotType
from pitchvision_worker.stages.calibrate import CalibrateOutput, run_calibrate
from pitchvision_worker.stages.infer import run_infer
from synth_pitch_clip import FakeDetector, FakeEmbedder, write_clip

WIDE = ShotTimeline(shots=[Shot(t0_ms=0, t1_ms=8000, type=ShotType.main_wide, conf=0.9)])


@pytest.fixture(scope="module")
def clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_clip(tmp_path_factory.mktemp("clip") / "normalized.mp4")


@pytest.fixture(scope="module")
def calibrated(clip: Path, tmp_path_factory: pytest.TempPathFactory) -> CalibrateOutput:
    return run_calibrate(clip, WIDE, tmp_path_factory.mktemp("models"), PipelineConfig(), n_frames=30,
                         detector=FakeDetector(), embedder=FakeEmbedder())


@pytest.mark.parametrize("stride", [1, 2])
def test_infer_segment(clip: Path, calibrated: CalibrateOutput, tmp_path: Path, stride: int) -> None:
    seg = Segment(index=2, t0_ms=2000, t1_ms=6000)
    det, emb = FakeDetector(), FakeEmbedder()
    out = run_infer(clip, seg, calibrated.team_model, tmp_path, PipelineConfig(frame_stride=stride), detector=det,
                    embedder=emb, votes_per_track=5)
    assert out.paths["detections"] == tmp_path / "detections.parquet"
    assert not (tmp_path / "_SUCCESS").exists()  # the task layer writes it after the upload
    assert out.frames == 100 // stride == det.calls and out.fps_measured > 0
    t = read_detections(out.paths["detections"])
    c = {k: t.column(k).to_numpy() for k in t.column_names}
    frames = np.unique(c["frame_idx"])
    assert frames[0] == 50 and frames[-1] == 149 - (stride - 1) and np.all(frames % stride == 0)
    np.testing.assert_array_equal(c["ts_ms"], c["frame_idx"] * 40)
    assert np.isnan(c["px"]).all() and np.isnan(c["py"]).all()

    players = c["cls"] == CLS_PLAYER
    ids = np.unique(c["track_id"][players])
    assert len(ids) == 13 and ids.min() >= 1  # 12 players + referee, no fragmentation on linear motion
    assert out.stats["tracks"] == 13 and out.stats["median_players_per_frame"] == 13
    # one team per track, two teams of 6, the referee (outlier kit) -1
    team_of = {int(i): set(c["team"][players & (c["track_id"] == i)].tolist()) for i in ids}
    assert all(len(v) == 1 for v in team_of.values())
    counts = sorted(next(iter(v)) for v in team_of.values())
    assert counts.count(0) == 6 and counts.count(1) == 6 and counts.count(-1) == 1
    # same-colour tracks share a team: x1 of red/blue boxes are interleaved by construction, so check via colour
    assert emb.calls <= 13 * 5

    balls = c["cls"] == CLS_BALL
    assert balls.sum() == out.frames and (c["track_id"][balls] == NO_TRACK).all() and (c["team"][balls] == -1).all()
    md = detections_metadata(out.paths["detections"])
    assert md["meta"]["segment"] == seg.model_dump() and md["meta"]["stride"] == stride


def test_infer_off_pitch_cannot_start_tracks(tmp_path: Path, calibrated: CalibrateOutput) -> None:
    clip = write_clip(tmp_path / "nograss.mp4", 2.0, grass=False)  # no grass anywhere: nothing is on the pitch
    out = run_infer(clip, Segment(index=0, t0_ms=0, t1_ms=2000), calibrated.team_model, tmp_path / "o",
                    PipelineConfig(), detector=FakeDetector(), embedder=FakeEmbedder())
    t = read_detections(out.paths["detections"])
    cls = t.column("cls").to_numpy()
    assert (cls == CLS_PLAYER).sum() == 0
    assert (cls == CLS_BALL).sum() == out.frames and not any(t.column("on_pitch").to_pylist())


def test_infer_rejects_other_embedder(clip: Path, calibrated: CalibrateOutput, tmp_path: Path) -> None:
    model = TeamModel.load(calibrated.team_model)
    model.embedder_sha256 = "a" * 64
    with pytest.raises(RuntimeError, match="not comparable"):
        run_infer(clip, Segment(index=0, t0_ms=0, t1_ms=400), model, tmp_path, PipelineConfig(),
                  detector=FakeDetector(), embedder=FakeEmbedder(), embedder_sha256="b" * 64)
