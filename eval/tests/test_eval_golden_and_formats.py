"""M1-CV-03 — golden.json build, file formats, SP-09 conversion, baseline, run_shots end to end."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
from pveval.formats import (
    SHOT_TIMELINES,
    ShotSample,
    load_coco,
    read_shot_predictions,
    read_shot_samples,
    read_team_labels,
    video_of,
    write_shot_samples,
)
from pveval.golden import GOLDEN_JSON, REPO, STRATA, build_golden, load_golden
from pveval.metrics import NON_WIDE, WIDE
from pveval.shots import colour_features, green_rule, sp09_samples

MANIFEST = REPO / "data" / "golden" / "manifest.json"


# --- golden set ----------------------------------------------------------------------------------


def test_golden_json_is_up_to_date() -> None:
    built = build_golden(json.loads(MANIFEST.read_text()))
    assert json.loads(GOLDEN_JSON.read_text()) == built, "run: uv run python eval/build_golden.py"


def test_golden_strata() -> None:
    g = load_golden()
    assert len(g) == 19
    for m in g.values():
        for k in (*STRATA, "competition", "channel", "gender", "age_group", "kit_similarity"):
            assert k in m
        assert m["split"] in ("dev", "test") and m["camera_source"] == "broadcast"
    cats = {c: [m for m in g.values() if m["category"] == c] for c in ("men", "women", "youth")}
    assert [len(v) for v in cats.values()] == [7, 7, 5]
    assert all(any(m["split"] == "test" for m in v) for v in cats.values())  # every category held out
    assert g["EhqJ8-g1YvU"]["fps_class"] == "50" and g["BukNW8GRI5A"]["fps_class"] == "60"
    assert g["AZd7qfwQtfM"]["source_quality"] == "sd_upscale"


def test_golden_split_deterministic() -> None:
    m = json.loads(MANIFEST.read_text())
    a ={x["id"]: x["split"] for x in build_golden(m)["matches"]}
    b = {x["id"]: x["split"] for x in build_golden(list(reversed(m)))["matches"]}
    assert a == b


# --- formats -------------------------------------------------------------------------------------


def test_video_of_handles_underscore_ids() -> None:
    assert video_of("_DfZB1Y-E_k/000123.jpg") == "_DfZB1Y-E_k"
    assert video_of("_DfZB1Y-E_k_000123.jpg") == "_DfZB1Y-E_k"
    assert video_of("frames/caZwnRXhkAk/000001.png") == "caZwnRXhkAk"


def test_load_coco(tmp_path: Path) -> None:
    gt = {
        "images": [{"id": 1, "file_name": "vidA/000010.jpg"}, {"id": 2, "file_name": "vidB/000010.jpg"}],
        "categories": [{"id": 1, "name": "player"}, {"id": 2, "name": "ball"}],
        "annotations": [
            {"id": 1, "image_id": 1, "category_id": 1, "bbox": [10, 10, 20, 40]},
            {"id": 2, "image_id": 2, "category_id": 2, "bbox": [5, 5, 4, 4]},
            {"id": 3, "image_id": 2, "category_id": 1, "bbox": [0, 0, 99, 99], "iscrowd": 1},
        ],
    }
    preds = [{"image_id": 1, "category_id": 1, "bbox": [11, 10, 20, 40], "score": 0.7}]
    (tmp_path / "gt.json").write_text(json.dumps(gt))
    (tmp_path / "p.json").write_text(json.dumps(preds))
    c = load_coco(tmp_path / "gt.json", tmp_path / "p.json")
    assert c.image_video == {"1": "vidA", "2": "vidB"}
    assert c.class_names == {0: "player", 3: "ball"}
    assert len(c.gts) == 2 and c.gts[0].xyxy == (10, 10, 30, 50) and c.gts[1].cls == 3
    assert c.preds[0].score == 0.7 and c.preds[0].cls == 0


def test_shot_formats_roundtrip_and_timelines(tmp_path: Path) -> None:
    gt = [ShotSample("v", 5000, WIDE), ShotSample("v", 25000, NON_WIDE), ShotSample("w", 5000, WIDE)]
    write_shot_samples(tmp_path / "gt.json", gt)
    assert read_shot_samples(tmp_path / "gt.json") == gt
    tl = {"format": SHOT_TIMELINES, "timelines": {"v": {"shots": [
        {"t0_ms": 0, "t1_ms": 20000, "type": "main_wide", "conf": 0.9},
        {"t0_ms": 20000, "t1_ms": 30000, "type": "replay", "conf": 0.8},
    ]}}}
    (tmp_path / "tl.json").write_text(json.dumps(tl))
    pred = read_shot_predictions(tmp_path / "tl.json", gt)
    assert pred == {("v", 5000): "main_wide", ("v", 25000): "replay", ("w", 5000): None}


def test_team_labels(tmp_path: Path) -> None:
    (tmp_path / "t.json").write_text(json.dumps({"format": "pv-team-labels/v1", "items": [
        {"video": "v", "frame_idx": 10, "obj": 3, "team": 1}]}))
    assert read_team_labels(tmp_path / "t.json") == {("v", 10, "3"): 1}
    (tmp_path / "bad.json").write_text("{}")
    with pytest.raises(ValueError):
        read_team_labels(tmp_path / "bad.json")


# --- SP-09 conversion + baseline + run_shots ------------------------------------------------------


def pitch_frame(wide: bool) -> np.ndarray:
    img = np.full((360, 640, 3), (60, 60, 60), np.uint8)
    if wide:
        img[60:] = (40, 140, 40)              # BGR grass over the lower 5/6
        img[200:204, :] = (255, 255, 255)     # a pitch line
    return img


def test_green_rule() -> None:
    assert green_rule(colour_features(pitch_frame(True)))[0] == WIDE
    assert green_rule(colour_features(pitch_frame(False)))[0] == NON_WIDE
    assert colour_features(pitch_frame(True))["lines"] > 0


@pytest.fixture
def sp09_dir(tmp_path: Path) -> Path:
    d = tmp_path / "sp09"
    (d / "frames").mkdir(parents=True)
    index, nonwide = [], {}
    for vid in ("BukNW8GRI5A", "_DfZB1Y-E_k"):
        nonwide[vid] = [1]
        for k, t in enumerate((5, 25, 45)):
            name = f"{vid}_{t:04d}.jpg"
            cv2.imwrite(str(d / "frames" / name), pitch_frame(k != 1))
            index.append({"video": vid, "t": t, "k": k, "file": name})
    (d / "index.json").write_text(json.dumps(index))
    (d / "labels_nonwide.json").write_text(json.dumps({"_doc": "test", **nonwide}))
    return d


def test_sp09_samples(sp09_dir: Path) -> None:
    samples, files = sp09_samples(sp09_dir)
    assert [(s.video, s.t_ms, s.label) for s in samples[:3]] == [
        ("BukNW8GRI5A", 5000, WIDE), ("BukNW8GRI5A", 25000, NON_WIDE), ("BukNW8GRI5A", 45000, WIDE)]
    assert files[("_DfZB1Y-E_k", 45000)] == "_DfZB1Y-E_k_0045.jpg"


def test_run_shots_end_to_end(sp09_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                              capsys: pytest.CaptureFixture[str]) -> None:
    import run_shots

    out = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", ["run_shots.py", "--sp09-dir", str(sp09_dir), "--out-dir", str(out)])
    run_shots.main()
    report = json.loads((out / "shots_report.json").read_text())
    assert report["overall"]["n"] == 6 and report["overall"]["accuracy"] == 1.0
    assert set(report["by_group"]["category"]) == {"men", "youth"}
    assert len(read_shot_samples(out / "shot_labels.json")) == 6
    assert "by category" in capsys.readouterr().out


def test_run_detection_and_teams_scripts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                         capsys: pytest.CaptureFixture[str]) -> None:
    import run_detection
    import run_teams

    gt = {
        "images": [{"id": 1, "file_name": "BukNW8GRI5A/002500.jpg"}, {"id": 2, "file_name": "_DfZB1Y-E_k/002500.jpg"}],
        "categories": [{"id": 1, "name": "player"}, {"id": 2, "name": "ball"}],
        "annotations": [{"id": 1, "image_id": 1, "category_id": 1, "bbox": [10, 10, 20, 40]},
                        {"id": 2, "image_id": 2, "category_id": 2, "bbox": [5, 5, 4, 4]}],
    }
    pred = [{"image_id": 1, "category_id": 1, "bbox": [10, 10, 20, 40], "score": 1}]
    (tmp_path / "gt.json").write_text(json.dumps(gt))
    (tmp_path / "p.json").write_text(json.dumps(pred))
    monkeypatch.setattr(sys, "argv", ["x", "--gt", str(tmp_path / "gt.json"), "--pred", str(tmp_path / "p.json"),
                                      "--out", str(tmp_path / "det.json")])
    run_detection.main()
    det = json.loads((tmp_path / "det.json").read_text())
    assert det["overall"]["player"] == 1.0 and det["overall"]["ball"] == 0.0
    assert det["by_group"]["category"]["men"]["mAP50"] == 1.0

    items = [{"video": "BukNW8GRI5A", "frame_idx": 1, "obj": i, "team": t} for i, t in enumerate([0, 0, 1, -1])]
    team_pred = [dict(it, team=1 - it["team"]) for it in items[:3]]
    (tmp_path / "tg.json").write_text(json.dumps({"format": "pv-team-labels/v1", "items": items}))
    (tmp_path / "tp.json").write_text(json.dumps({"format": "pv-team-labels/v1", "items": team_pred}))
    monkeypatch.setattr(sys, "argv", ["x", "--gt", str(tmp_path / "tg.json"), "--pred", str(tmp_path / "tp.json"),
                                      "--out", str(tmp_path / "teams.json")])
    run_teams.main()
    teams = json.loads((tmp_path / "teams.json").read_text())
    assert teams["overall"] == {"n": 3, "accuracy": 1.0}
    assert "by region" in capsys.readouterr().out
