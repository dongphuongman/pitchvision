"""S3 calibrate on a synthetic clip with an injected fake detector / embedder (no model downloads).

Golden-data runs: eval/run_pipeline_w3.py (needs PITCHVISION_GOLDEN_DIR + the registered models).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from cv_core.teams import TeamModel, TeamSeparationFailed, torso_crop
from cv_core.teams.calibrate import read_frames
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
    # display colours (S8 teams / manifest): one red-ish, one blue-ish cluster
    colors = raw["stats"]["colors"]
    assert set(colors) == {"0", "1"}
    rgb = sorted((int(c[1:3], 16), int(c[5:7], 16)) for c in colors.values())  # (r, b)
    assert rgb[0][1] > rgb[0][0] and rgb[1][0] > rgb[1][1]  # a blue team and a red team
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
    # diagnostics (uploaded by the executor although the task fails): grid + SCR-14 manual-mode candidates
    assert (tmp_path / "m" / "diagnostics" / "team_samples.jpg").exists()
    cand = json.loads((tmp_path / "m" / "diagnostics" / "team_candidates.json").read_text())
    assert cand["frames"] and all(len(b) == 4 for f in cand["frames"] for b in f["boxes"])




def _colour_of(clip: Path, t_ms: int, box: list[int]) -> str:
    (_, img), = read_frames(clip, [round(t_ms * 25 / 1000)])
    x1, y1, x2, y2 = box
    b, _, r = img[y1:y2, x1:x2].reshape(-1, 3).mean(0)
    return "red" if r > b else "blue"


def test_calibrate_with_seeds_follows_the_user(clip: Path, calibrated: CalibrateOutput, tmp_path: Path) -> None:
    """SCR-14 manual mode: seed boxes from team_candidates.json; red players → team "0" (the default run put the
    larger / first cluster wherever it liked) — the seeded model must put red in cluster 0 and blue in 1."""
    cand = json.loads((calibrated.team_model.parent / "team_candidates.json").read_text())
    assert cand["fps"] == 25 and 0 < len(cand["frames"]) <= 20
    seeds: dict[str, list[dict[str, object]]] = {"0": [], "1": []}
    for f in cand["frames"]:
        for box in f["boxes"]:
            colour = _colour_of(clip, f["t_ms"], box)
            team = "0" if colour == "red" else "1"
            if len(seeds[team]) < 3:
                seeds[team].append({"t_ms": f["t_ms"], "box": box})
    assert all(len(v) == 3 for v in seeds.values())
    for mapping in ({"0": "0", "1": "1"}, {"0": "1", "1": "0"}):
        s = {mapping[k]: v for k, v in seeds.items()}
        out = run_calibrate(clip, WIDE, tmp_path / mapping["0"], PipelineConfig(), n_frames=30,
                            detector=FakeDetector(), embedder=FakeEmbedder(), seeds=s)
        model = TeamModel.load(out.team_model)
        assert model.stats["seeded"] and model.stats["seed_agreement"] == 1.0
        emb = FakeEmbedder()
        for team, picks in s.items():
            for pk in picks:
                (_, img), = read_frames(clip, [round(int(pk["t_ms"]) * 25 / 1000)])  # type: ignore[call-overload]
                crop = torso_crop(img, np.asarray(pk["box"], np.float32))
                assert int(model.predict(emb([crop]))[0]) == int(team)
