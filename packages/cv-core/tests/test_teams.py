"""Team model: fit / predict on synthetic embedding clusters, JSON round-trip, failure conditions."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from cv_core.teams import (
    NO_TEAM,
    FitParams,
    TeamModel,
    TeamSeparationFailed,
    fit_team_model,
    kmeans,
    preprocess,
    representative,
    silhouette,
    torso_crop,
    vote,
)

D = 64


def _unit(x: np.ndarray) -> np.ndarray:
    return x / np.linalg.norm(x, axis=1, keepdims=True)


def _clusters(n0: int, n1: int, n_out: int = 0, spread: float = 0.15, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    centres = rng.normal(size=(2 + max(n_out, 1), D))
    parts, labels = [], []
    for j, n in enumerate((n0, n1)):
        parts.append(centres[j] + spread * rng.normal(size=(n, D)))
        labels += [j] * n
    for k in range(n_out):  # referee / goalkeepers: their own kits, far from both teams
        parts.append(centres[2 + k % max(n_out, 1)][None] + spread * rng.normal(size=(1, D)))
        labels.append(NO_TEAM)
    return _unit(np.concatenate(parts)), np.array(labels)


def _agreement(pred: np.ndarray, truth: np.ndarray) -> float:
    m = truth >= 0
    a = float((pred[m] == truth[m]).mean())
    return max(a, float((pred[m] == 1 - truth[m]).mean()))


def test_fit_predict_two_teams_with_outliers() -> None:
    emb, truth = _clusters(300, 250, n_out=12)
    model = fit_team_model(emb, embedder="test")
    pred = model.predict(emb)
    assert _agreement(pred, truth) > 0.99
    assert (pred[truth == NO_TEAM] == NO_TEAM).mean() >= 0.9       # far-away kits → -1
    assert (pred[truth >= 0] == NO_TEAM).mean() < 0.05
    assert model.stats["cluster_sizes"][0] >= model.stats["cluster_sizes"][1]   # team 0 = larger cluster
    assert model.stats["cv_fisher"] > 8 and model.stats["split"]


def test_json_round_trip(tmp_path: Path) -> None:
    emb, _ = _clusters(100, 90)
    model = fit_team_model(emb, embedder="siglip-test", embedder_sha256="abc")
    model.samples = {str(k): [{"crop": i} for i in v] for k, v in representative(model, emb).items()}
    p = model.save(tmp_path / "team_model.json")
    raw = json.loads(p.read_text())
    assert raw["format"] == "pv-team-model/v1" and raw["embedder"] == {"name": "siglip-test", "sha256": "abc"}
    back = TeamModel.load(p)
    np.testing.assert_array_equal(back.predict(emb), model.predict(emb))
    assert back.samples == model.samples and len(back.samples["0"]) == 3
    with pytest.raises(ValueError):
        TeamModel.from_dict({**raw, "format": "pickle"})


def test_representative_are_closest_members() -> None:
    emb, _ = _clusters(80, 80)
    model = fit_team_model(emb, embedder="t")
    reps = representative(model, emb, n=3)
    d = model.distances(emb)
    lab = model.predict(emb)
    for j in (0, 1):
        assert len(reps[j]) == 3 and all(lab[i] == j for i in reps[j])
        assert max(d[reps[j], j]) <= np.sort(d[lab == j, j])[2] + 1e-12


@pytest.mark.parametrize(("n0", "n1", "spread", "reason"), [
    (30, 5, 0.15, "too_few_crops"),
    (400, 20, 0.15, "imbalanced|not_separable"),
])
def test_failure_conditions(n0: int, n1: int, spread: float, reason: str) -> None:
    emb, _ = _clusters(n0, n1, spread=spread)
    with pytest.raises(TeamSeparationFailed) as ei:
        fit_team_model(emb, embedder="t")
    assert ei.value.reason in reason.split("|") and ei.value.code == "TEAM_SEPARATION_FAILED"


def test_single_blob_not_separable() -> None:
    rng = np.random.default_rng(3)
    emb = _unit(rng.normal(size=D) + 0.3 * rng.normal(size=(400, D)))
    with pytest.raises(TeamSeparationFailed) as ei:
        fit_team_model(emb, embedder="t", params=FitParams(min_share=0.0))
    assert ei.value.reason == "not_separable" and ei.value.stats["cv_fisher"] < 4


def test_kmeans_and_silhouette_basics() -> None:
    x = np.r_[np.zeros((20, 2)), np.ones((20, 2)) * 10] + np.random.default_rng(0).normal(0, 0.1, (40, 2))
    _c, lab, _ = kmeans(x, 2, seed=1)
    assert len(set(lab[:20])) == 1 and len(set(lab[20:])) == 1 and lab[0] != lab[-1]
    assert silhouette(x, lab) > 0.9
    assert silhouette(x, np.zeros(40, int)) == 0.0


def test_vote() -> None:
    assert vote([0, 0, 1]) == 0 and vote([1, -1, 1, 0]) == 1
    assert vote([0, 1]) == NO_TEAM and vote([]) == NO_TEAM and vote([-1, -1, 0]) == NO_TEAM


def test_crops_and_preprocess() -> None:
    img = np.zeros((100, 200, 3), np.uint8)
    img[20:60, 50:70] = (0, 0, 255)
    c = torso_crop(img, [45, 10, 75, 90])
    assert c is not None and c.shape[2] == 3
    assert torso_crop(img, [0, 0, 4, 4]) is None
    x = preprocess([c, img])
    assert x.shape == (2, 3, 224, 224) and x.dtype == np.float32 and x.min() >= -1.0 and x.max() <= 1.0
