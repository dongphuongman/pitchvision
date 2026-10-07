"""Pitch calibration stage (OPTIONAL, M1-CV-11/12, AD §6.3): one segment → ``out_dir/homography.parquet``.

Runs only when ``models/registry.yaml`` has a ``pitch_keypoints`` entry whose weights are present locally (sha256
checked); otherwise it returns ``status = "unavailable"`` without writing anything and the job's pitch calibration
is ``unavailable`` (``px, py`` stay NaN, tiles carry ``hq = null``). The SP-03 roboflow spike model is AGPL and must
never be registered (G-LEGAL); M1-CV-05 delivers the licensed one. Until then the registry carries PnLCalib's
keypoint HRNet (``format: heatmap-world/v1``, ``research_only``): loaded only on a local machine that opted in
(``research_models_allowed``) — an internal demo of the tactical map, never for customers.

Per segment (``[t0, t1)`` on the normalized timeline):

1. decode every ``config.keypoint_every_n``-th frame (sequential decode, one seek);
2. keypoint model → ``estimate_homography`` → ``h_quality`` by line alignment (never the fit residual, SP-03);
3. ``HomographySmoother.update`` with the measurement; ``reset`` at every shot cut inside the segment (``cuts_ms``
   from the S2b timeline: never smooth across a cut);
4. ``smoother.at(f)`` for EVERY frame of the segment → per-frame image→pitch H (NaN rows when the smoother has
   nothing reliable) + smoothed quality;
5. camera tracking (default): every frame is decoded, ``cv_core.pitch.motion.CameraMotion`` estimates the
   frame-to-frame camera motion and ``propagate`` chains the smoothed keyframe homographies forwards / backwards
   through it (never across a cut) — frames it reaches replace the step-4 interpolation.

``run_infer`` is untouched: S4 and this stage are separate GPU tasks of the same segment; S5 joins their outputs.
"""
from __future__ import annotations

import logging
import math
import os
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np
from cv_core.io.parquet import HOMOGRAPHY_VERSION, write_homography
from cv_core.io.video import VideoReader
from cv_core.pitch import (
    HeatmapPitchModel,
    HomographySmoother,
    PitchKeypointModel,
    PitchKeypoints,
    estimate_homography,
    h_quality,
)
from cv_core.pitch.motion import CameraMotion, propagate
from cv_core.registry import ModelChecksumError, ModelRegistry
from cv_core.segments import segment_frames
from pitchvision_schemas import PipelineConfig, Segment

log = logging.getLogger(__name__)

PITCH_MODEL = "pitch_keypoints"   # registry entry name (M1-CV-05); absent in M1 → stage is "unavailable"
HOMOGRAPHY_NAME = "homography.parquet"
SMOOTH_WINDOW = 15                # AD §6.3 HomographySmoother(window=15)
BATCH = 8

KeypointModel = Callable[[Sequence[np.ndarray]], list[PitchKeypoints]]
PitchStatus = Literal["ok", "unavailable"]


@dataclass(frozen=True)
class PitchOutput:
    status: PitchStatus
    paths: dict[str, Path]               # {"homography": out_dir/homography.parquet} when ok
    frames: int = 0                      # frames with a row (= segment length)
    keypoint_frames: int = 0             # frames the keypoint model ran on
    stats: dict[str, Any] = field(default_factory=dict)


def research_models_allowed(env: Mapping[str, str] | None = None) -> bool:
    """Research-only weights (``research_only: true`` in the registry, e.g. PnLCalib) run only on a developer
    machine that opted in: ``PV_ALLOW_RESEARCH_MODELS=1`` and ``PV_ENV`` local / test. Staging and production
    never load them, whatever the flag says."""
    e = os.environ if env is None else env
    return e.get("PV_ALLOW_RESEARCH_MODELS") == "1" and (e.get("PV_ENV") or "local") in ("local", "test")


def _refused(reg: ModelRegistry) -> str | None:
    e = reg.entries.get(PITCH_MODEL)
    if e is not None and e.extra.get("research_only") and not research_models_allowed():
        return (f"{PITCH_MODEL!r} is research-only ({e.licence}); allowed only with PV_ALLOW_RESEARCH_MODELS=1 "
                "in PV_ENV=local")
    return None


def pitch_model_registered(registry: ModelRegistry | None = None) -> bool:
    """Cheap check used by the orchestrator (the registry ships in the image; weights live on the GPU box)."""
    try:
        reg = registry or ModelRegistry.load()
    except Exception as e:  # unreadable registry = no optional stages
        log.warning("model registry unreadable: %r", e)
        return False
    return PITCH_MODEL in reg.entries and _refused(reg) is None


def load_pitch_model(registry: ModelRegistry | None = None, runtime: Any | None = None
                     ) -> tuple[KeypointModel | None, str]:
    """→ (model, "ok") or (None, why it is unavailable). Never raises for a missing / mismatching model."""
    try:
        reg = registry or ModelRegistry.load()
    except Exception as e:
        return None, f"model registry unreadable: {e}"
    if PITCH_MODEL not in reg.entries:
        return None, f"no {PITCH_MODEL!r} entry in models/registry.yaml"
    if (why := _refused(reg)) is not None:
        return None, why
    try:
        path = reg.resolve(PITCH_MODEL)
    except (FileNotFoundError, ModelChecksumError) as e:
        return None, str(e)
    if runtime is None:
        from pitchvision_worker.stages.models import runtime_from_env

        runtime = runtime_from_env()
    entry = reg.entries[PITCH_MODEL]
    if entry.extra.get("format") == "heatmap-world/v1":
        return HeatmapPitchModel(runtime.model(str(path)), world=entry.extra["keypoints_world"],
                                 threshold=float(entry.extra.get("threshold", 0.5))), "ok"  # fmt: skip
    return PitchKeypointModel(runtime.model(str(path))), "ok"


def run_pitch(normalized: Path, segment: Segment, out_dir: Path, config: PipelineConfig | None = None, *,
              model: KeypointModel | None = None, cuts_ms: Sequence[int] = (),
              registry: ModelRegistry | None = None, track_camera: bool = True) -> PitchOutput:
    cfg = config or PipelineConfig()
    t_all = time.perf_counter()
    if model is None:
        model, reason = load_pitch_model(registry)
        if model is None:
            log.info("pitch seg %d: unavailable (%s)", segment.index, reason)
            return PitchOutput("unavailable", {}, stats={"reason": reason})
    fps = float(cfg.target_fps)
    i0, i1 = segment_frames(segment, fps)
    every = cfg.keypoint_every_n
    # every frame is decoded when tracking the camera (cheap); the keypoint model only runs every `every` frames
    reader = VideoReader(normalized, segment.t0_ms / 1000, segment.t1_ms / 1000, stride=1 if track_camera else every,
                         target_fps=fps)  # fmt: skip
    n_kf = max(1, (i1 - i0) // every + 2)
    smoother = HomographySmoother(SMOOTH_WINDOW, image_size=reader.size, history=max(512, n_kf + 16))
    cuts = sorted({math.floor(c * fps / 1000 + 0.5) for c in cuts_ms} & set(range(i0 + 1, i1)))
    ci = 0
    kp_frames = ok_frames = 0
    qualities: list[float] = []
    reasons: dict[str, int] = {}
    camera = CameraMotion()
    motion: dict[int, np.ndarray | None] = {}
    keyframes: list[tuple[int, np.ndarray]] = []

    def measure(batch: list[tuple[int, np.ndarray]]) -> None:
        nonlocal kp_frames, ok_frames, ci
        for (idx, image), kp in zip(batch, model([im for _, im in batch]), strict=True):
            while ci < len(cuts) and cuts[ci] <= idx:
                smoother.reset(cuts[ci])
                ci += 1
            kp_frames += 1
            est = estimate_homography(kp)
            q = h_quality(image, est.H_inv) if est.ok and est.H_inv is not None else 0.0
            if est.ok:
                ok_frames += 1
                qualities.append(q)
            else:
                key = est.reason.split(" (")[0]
                reasons[key] = reasons.get(key, 0) + 1
            smoother.update(idx, est.H, q)

    cut_set = set(cuts)
    for batch in reader.batches(BATCH):
        for fr in batch:
            if track_camera:
                if fr.idx in cut_set:
                    camera.reset()
                motion[fr.idx] = camera.step(fr.image)
            if (fr.idx - i0) % every == 0 or not track_camera:
                keyframes.append((fr.idx, fr.image))
        if len(keyframes) >= BATCH:
            measure(keyframes)
            keyframes = []
    if keyframes:
        measure(keyframes)
    for c in cuts[ci:]:
        smoother.reset(c)

    frames = np.arange(i0, i1, dtype=np.int64)
    Hs = np.full((len(frames), 3, 3), np.nan)
    qs = np.zeros(len(frames), np.float32)
    for k, f in enumerate(frames.tolist()):
        H, q = smoother.at(f)
        if H is not None:
            Hs[k], qs[k] = H, q
    tracked = 0
    if track_camera:
        keys = {k: (Hs[k], float(qs[k])) for k, f in enumerate(frames.tolist())
                if (f - i0) % every == 0 and np.isfinite(Hs[k, 0, 0])}  # fmt: skip
        prop, pq = propagate(len(frames), keys, [motion.get(int(f)) for f in frames],
                             {c - i0 for c in cuts}, image_size=reader.size)  # fmt: skip
        for k, H in enumerate(prop):
            if H is not None:
                Hs[k], qs[k] = H, pq[k]
                tracked += 1
    with_h = int(np.isfinite(Hs[:, 0, 0]).sum())
    stats = {"segment": segment.model_dump(), "keypoint_every_n": every, "keypoint_frames": kp_frames,
             "estimates_ok": ok_frames, "frames_with_h": with_h, "frames_camera_tracked": tracked, "cuts": cuts,
             "mean_measured_quality": round(float(np.mean(qualities)), 4) if qualities else None,
             "rejections": reasons}  # fmt: skip
    meta = {"stage": "pitch", "version": HOMOGRAPHY_VERSION, "fps": fps, "segment": segment.model_dump(),
            "stats": stats}
    path = write_homography(Path(out_dir) / HOMOGRAPHY_NAME, frames, Hs, qs, meta)
    stats["elapsed_s"] = round(time.perf_counter() - t_all, 3)
    log.info("pitch seg %d [%d, %d) ms: %d keypoint frames, %d ok, H on %d/%d frames", segment.index, segment.t0_ms,
             segment.t1_ms, kp_frames, ok_frames, with_h, len(frames))
    return PitchOutput("ok", {"homography": path}, frames=len(frames), keypoint_frames=kp_frames, stats=stats)
