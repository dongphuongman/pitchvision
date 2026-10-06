"""Pitch calibration stage (OPTIONAL, M1-CV-11/12, AD §6.3): one segment → ``out_dir/homography.parquet``.

Runs only when ``models/registry.yaml`` has a ``pitch_keypoints`` entry whose weights are present locally (sha256
checked); otherwise it returns ``status = "unavailable"`` without writing anything and the job's pitch calibration
is ``unavailable`` (``px, py`` stay NaN, tiles carry ``hq = null``). No keypoint model is registered in M1 yet: the
SP-03 roboflow spike model is AGPL and must never be registered (G-LEGAL); M1-CV-05 delivers the licensed one.

Per segment (``[t0, t1)`` on the normalized timeline):

1. decode every ``config.keypoint_every_n``-th frame (sequential decode, one seek);
2. keypoint model → ``estimate_homography`` → ``h_quality`` by line alignment (never the fit residual, SP-03);
3. ``HomographySmoother.update`` with the measurement; ``reset`` at every shot cut inside the segment (``cuts_ms``
   from the S2b timeline: never smooth across a cut);
4. ``smoother.at(f)`` for EVERY frame of the segment → per-frame image→pitch H (NaN rows when the smoother has
   nothing reliable) + smoothed quality.

``run_infer`` is untouched: S4 and this stage are separate GPU tasks of the same segment; S5 joins their outputs.
"""
from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np
from cv_core.io.parquet import HOMOGRAPHY_VERSION, write_homography
from cv_core.io.video import VideoReader
from cv_core.pitch import HomographySmoother, PitchKeypointModel, PitchKeypoints, estimate_homography, h_quality
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


def pitch_model_registered(registry: ModelRegistry | None = None) -> bool:
    """Cheap check used by the orchestrator (the registry ships in the image; weights live on the GPU box)."""
    try:
        reg = registry or ModelRegistry.load()
    except Exception as e:  # unreadable registry = no optional stages
        log.warning("model registry unreadable: %r", e)
        return False
    return PITCH_MODEL in reg.entries


def load_pitch_model(registry: ModelRegistry | None = None, runtime: Any | None = None
                     ) -> tuple[KeypointModel | None, str]:
    """→ (model, "ok") or (None, why it is unavailable). Never raises for a missing / mismatching model."""
    try:
        reg = registry or ModelRegistry.load()
    except Exception as e:
        return None, f"model registry unreadable: {e}"
    if PITCH_MODEL not in reg.entries:
        return None, f"no {PITCH_MODEL!r} entry in models/registry.yaml"
    try:
        path = reg.resolve(PITCH_MODEL)
    except (FileNotFoundError, ModelChecksumError) as e:
        return None, str(e)
    if runtime is None:
        from pitchvision_worker.stages.models import runtime_from_env

        runtime = runtime_from_env()
    return PitchKeypointModel(runtime.model(str(path))), "ok"


def run_pitch(normalized: Path, segment: Segment, out_dir: Path, config: PipelineConfig | None = None, *,
              model: KeypointModel | None = None, cuts_ms: Sequence[int] = (),
              registry: ModelRegistry | None = None) -> PitchOutput:
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
    reader = VideoReader(normalized, segment.t0_ms / 1000, segment.t1_ms / 1000, stride=every, target_fps=fps)
    n_kf = max(1, (i1 - i0) // every + 2)
    smoother = HomographySmoother(SMOOTH_WINDOW, image_size=reader.size, history=max(512, n_kf + 16))
    cuts = sorted({math.floor(c * fps / 1000 + 0.5) for c in cuts_ms} & set(range(i0 + 1, i1)))
    ci = 0
    kp_frames = ok_frames = 0
    qualities: list[float] = []
    reasons: dict[str, int] = {}
    for batch in reader.batches(BATCH):
        kps = model([f.image for f in batch])
        for fr, kp in zip(batch, kps, strict=True):
            while ci < len(cuts) and cuts[ci] <= fr.idx:
                smoother.reset(cuts[ci])
                ci += 1
            kp_frames += 1
            est = estimate_homography(kp)
            q = h_quality(fr.image, est.H_inv) if est.ok and est.H_inv is not None else 0.0
            if est.ok:
                ok_frames += 1
                qualities.append(q)
            else:
                key = est.reason.split(" (")[0]
                reasons[key] = reasons.get(key, 0) + 1
            smoother.update(fr.idx, est.H, q)
    for c in cuts[ci:]:
        smoother.reset(c)

    frames = np.arange(i0, i1, dtype=np.int64)
    Hs = np.full((len(frames), 3, 3), np.nan)
    qs = np.zeros(len(frames), np.float32)
    for k, f in enumerate(frames.tolist()):
        H, q = smoother.at(f)
        if H is not None:
            Hs[k], qs[k] = H, q
    with_h = int(np.isfinite(Hs[:, 0, 0]).sum())
    stats = {"segment": segment.model_dump(), "keypoint_every_n": every, "keypoint_frames": kp_frames,
             "estimates_ok": ok_frames, "frames_with_h": with_h, "cuts": cuts,
             "mean_measured_quality": round(float(np.mean(qualities)), 4) if qualities else None,
             "rejections": reasons}  # fmt: skip
    meta = {"stage": "pitch", "version": HOMOGRAPHY_VERSION, "fps": fps, "segment": segment.model_dump(),
            "stats": stats}
    path = write_homography(Path(out_dir) / HOMOGRAPHY_NAME, frames, Hs, qs, meta)
    stats["elapsed_s"] = round(time.perf_counter() - t_all, 3)
    log.info("pitch seg %d [%d, %d) ms: %d keypoint frames, %d ok, H on %d/%d frames", segment.index, segment.t0_ms,
             segment.t1_ms, kp_frames, ok_frames, with_h, len(frames))
    return PitchOutput("ok", {"homography": path}, frames=len(frames), keypoint_frames=kp_frames, stats=stats)
