"""Optional pitch stage (M1-CV-11/12): fake keypoint model on a synthetic broadcast clip → homography.parquet.

No real keypoint model is used (none is registered in M1; the SP-03 roboflow spike model is AGPL and never ships).
The fake returns the template keypoints projected through the camera that rendered the frame.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import av
import numpy as np
import pytest
from cv_core.io.parquet import read_homography
from cv_core.pitch import FIFA_105x68, PitchKeypoints, project, render_lines
from cv_core.registry import ModelRegistry
from pitchvision_schemas import GpuTask, PipelineConfig, Segment, Shot, ShotTimeline, ShotType
from pitchvision_worker.compute import stages as stage_registry
from pitchvision_worker.stages.pitch import (
    PITCH_MODEL,
    load_pitch_model,
    pitch_model_registered,
    run_pitch,
)

W, H, FPS = 1280, 720, 25


def camera(target_x: float) -> np.ndarray:
    """pitch→image homography of a broadcast-like camera behind the near touchline aimed at (target_x, 34)."""
    C = np.array([52.5, 68 + 40.0, -22.0])
    T = np.array([target_x, 34.0, 0.0])
    f = (T - C) / np.linalg.norm(T - C)
    r = np.cross([0.0, 0.0, 1.0], f)
    r /= np.linalg.norm(r)
    d = np.cross(f, r)
    K = np.array([[1100.0, 0, W / 2], [0, 1100.0, H / 2], [0, 0, 1]])
    P = K @ np.c_[np.stack([r, d, f]), -np.stack([r, d, f]) @ C]
    Hm = P[:, [0, 1, 3]]
    return Hm / Hm[2, 2]


def render(H_p2i: np.ndarray) -> np.ndarray:
    img = np.full((H, W, 3), (40, 130, 45), np.uint8)
    return render_lines(H_p2i, (W, H), FIFA_105x68, thickness=3, image=img, colour=(235, 240, 240))


def write_clip(path: Path, cams: Sequence[np.ndarray]) -> Path:
    with av.open(str(path), "w") as c:
        st = c.add_stream("libx264", rate=FPS)
        st.width, st.height, st.pix_fmt = W, H, "yuv420p"
        st.options = {"g": str(2 * FPS), "crf": "18", "preset": "ultrafast"}
        for Hm in cams:
            for p in st.encode(av.VideoFrame.from_ndarray(render(Hm), format="bgr24")):
                c.mux(p)
        for p in st.encode():
            c.mux(p)
    return path


class FakeKeypointModel:
    """Returns the true template keypoints of frame ``calls × every`` (frames arrive in order)."""

    def __init__(self, cams: Sequence[np.ndarray], every: int) -> None:
        self.cams, self.every, self.calls = list(cams), every, 0

    def __call__(self, images: Sequence[np.ndarray]) -> list[PitchKeypoints]:
        out = []
        for _ in images:
            Hm = self.cams[self.calls * self.every]
            self.calls += 1
            xy = project(Hm, FIFA_105x68.keypoints).astype(np.float32)
            inside = (xy[:, 0] > 2) & (xy[:, 0] < W - 2) & (xy[:, 1] > 2) & (xy[:, 1] < H - 2)
            out.append(PitchKeypoints(xy, np.where(inside, 0.9, 0.02).astype(np.float32),
                                      np.array([0, 0, W, H], np.float32), 0.9, (W, H)))
        return out


@pytest.fixture(scope="module")
def two_shot_clip(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, list[np.ndarray]]:
    """2 s: camera A for 1 s, then a hard cut to camera B (different view)."""
    cams = [camera(40.0)] * FPS + [camera(70.0)] * FPS
    return write_clip(tmp_path_factory.mktemp("pitch") / "clip.mp4", cams), cams


def _pitch_xy(Hm: np.ndarray, px: tuple[float, float] = (640, 600)) -> np.ndarray:
    return project(np.linalg.inv(Hm), np.array([px], float))[0]


def test_run_pitch_writes_per_frame_h_and_resets_at_cut(two_shot_clip: tuple[Path, list[np.ndarray]],
                                                         tmp_path: Path) -> None:
    clip, cams = two_shot_clip
    seg = Segment(index=0, t0_ms=0, t1_ms=2000)
    cfg = PipelineConfig(keypoint_every_n=5)
    model = FakeKeypointModel(cams, 5)
    out = run_pitch(clip, seg, tmp_path, cfg, model=model, cuts_ms=[1000])
    assert out.status == "ok" and out.frames == 50 and out.keypoint_frames == 10 and model.calls == 10
    assert out.stats["cuts"] == [25] and out.stats["estimates_ok"] == 10
    frames, Hs, q = read_homography(out.paths["homography"])
    assert list(frames) == list(range(50)) and np.isfinite(Hs).all()
    assert (q > 0.5).all(), q  # synthetic lines align
    for f in (3, 22, 24):  # shot A, incl. the frames just before the cut (held, not blended with B)
        assert np.allclose(project(Hs[f], np.array([[640.0, 600.0]]))[0], _pitch_xy(cams[0]), atol=0.3)
    for f in (25, 27, 49):  # shot B from its first frame
        assert np.allclose(project(Hs[f], np.array([[640.0, 600.0]]))[0], _pitch_xy(cams[25]), atol=0.3)


def test_run_pitch_unavailable_without_registered_model(tmp_path: Path) -> None:
    seg = Segment(index=3, t0_ms=0, t1_ms=2000)
    empty = ModelRegistry({}, tmp_path)
    out = run_pitch(tmp_path / "missing.mp4", seg, tmp_path / "out", registry=empty)
    assert out.status == "unavailable" and out.paths == {} and PITCH_MODEL in out.stats["reason"]
    assert not (tmp_path / "out").exists() or not any((tmp_path / "out").iterdir())
    assert pitch_model_registered(empty) is False
    # the real registry has no pitch_keypoints entry in M1 (the AGPL spike model must never be registered)
    assert pitch_model_registered() is False


def test_registered_but_weights_missing_is_unavailable(tmp_path: Path) -> None:
    reg_file = tmp_path / "registry.yaml"
    reg_file.write_text(json.dumps({"models": [{"name": PITCH_MODEL, "version": "x", "file": "kp.onnx",
                                                "sha256": "0" * 64, "kind": "keypoints"}]}))
    reg = ModelRegistry.load(reg_file, tmp_path / "models")
    assert pitch_model_registered(reg) is True
    model, reason = load_pitch_model(reg)
    assert model is None and "not in" in reason


def test_adapter_passes_shot_cuts(two_shot_clip: tuple[Path, list[np.ndarray]], tmp_path: Path,
                                  monkeypatch: pytest.MonkeyPatch) -> None:
    clip, _ = two_shot_clip
    seen: dict[str, object] = {}

    def fake_run_pitch(normalized: Path, segment: Segment, out_dir: Path, config: PipelineConfig,
                       cuts_ms: Sequence[int] = ()) -> object:
        seen.update(segment=segment, cuts=list(cuts_ms))
        from pitchvision_worker.stages.pitch import PitchOutput

        return PitchOutput("unavailable", {}, stats={"reason": "test"})

    monkeypatch.setattr("pitchvision_worker.stages.pitch.run_pitch", fake_run_pitch)
    shots = tmp_path / "shots.json"
    shots.write_text(ShotTimeline(shots=[Shot(t0_ms=0, t1_ms=1000, type=ShotType.main_wide, conf=1),
                                         Shot(t0_ms=1000, t1_ms=2000, type=ShotType.main_wide, conf=1)]
                                  ).model_dump_json())  # fmt: skip
    seg = Segment(index=1, t0_ms=0, t1_ms=2000)
    task = GpuTask(task_id="j:pitch:001", stage="pitch", image_tag="t", output_prefix="p/", config=PipelineConfig(),
                   segment=seg)
    out = stage_registry.STAGES["pitch"](task, {"normalized": clip, "shots": shots}, tmp_path)
    assert seen == {"segment": seg, "cuts": [1000]}
    assert out.result["status"] == "unavailable" and out.result["segment"]["index"] == 1


def test_research_only_pitch_model_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    from pitchvision_worker.stages.pitch import load_pitch_model, pitch_model_registered, research_models_allowed

    assert research_models_allowed({}) is False
    assert research_models_allowed({"PV_ALLOW_RESEARCH_MODELS": "1"}) is True  # PV_ENV defaults to local
    assert research_models_allowed({"PV_ALLOW_RESEARCH_MODELS": "1", "PV_ENV": "staging"}) is False
    assert research_models_allowed({"PV_ALLOW_RESEARCH_MODELS": "1", "PV_ENV": "production"}) is False
    monkeypatch.delenv("PV_ALLOW_RESEARCH_MODELS", raising=False)
    assert pitch_model_registered() is False  # the repo registry's PnLCalib entry is research-only
    model, why = load_pitch_model()
    assert model is None and "research-only" in why
