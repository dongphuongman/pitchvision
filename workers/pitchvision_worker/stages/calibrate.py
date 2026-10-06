"""S3 calibrate (AD §6.2, M1-CV-09): fit the per-video team model from main_wide frames only.

``normalized.mp4`` + S2b ``ShotTimeline`` → ``out_dir/team_model.json`` (portable, no pickle) +
``out_dir/team_samples.jpg`` (crop grid per cluster: for eyeballing and the UI's team confirmation).

1. ``n_frames`` (default 48) frames spread over the ``config.analyze_shot_types`` shots, ≥ 1 s from cuts;
2. D-FINE persons → player candidates (feet on the grass mask, plausible size) → torso crops;
3. SigLIP-base embeddings → PCA → KMeans(2) → outlier radius (referees / goalkeepers → team -1);
4. ``TeamSeparationFailed`` (→ ``TEAM_SEPARATION_FAILED``) on too few crops, an imbalanced split or a low
   silhouette; ``NoWideShots`` (→ ``NO_WIDE_SHOTS``) when the timeline has no analysable footage.

The task layer uploads both files under ``keys.job_results_prefix(...) + "models/"``.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from cv_core.teams import Embedder, FitParams, TeamModel, TeamSeparationFailed, fit_team_model, representative
from cv_core.teams.calibrate import Detector, collect_crops, crops_grid, read_frames, sample_frame_indices
from pitchvision_schemas import PipelineConfig, ShotTimeline

from pitchvision_worker.stages.models import load_detector, load_embedder

log = logging.getLogger(__name__)

TEAM_MODEL_NAME = "team_model.json"
SAMPLES_NAME = "team_samples.jpg"
DEFAULT_FRAMES = 48

__all__ = ["CalibrateOutput", "NoWideShots", "TeamSeparationFailed", "run_calibrate"]


class NoWideShots(Exception):
    """The timeline has no analysable (main_wide) footage; the job fails with ``NO_WIDE_SHOTS``."""

    code = "NO_WIDE_SHOTS"


@dataclass(frozen=True)
class CalibrateOutput:
    team_model: Path          # out_dir/team_model.json
    samples: Path             # out_dir/team_samples.jpg
    frames: int               # frames sampled
    crops: int                # torso crops embedded
    stats: dict[str, Any] = field(default_factory=dict)
    timings_s: dict[str, float] = field(default_factory=dict)


def run_calibrate(normalized: Path, timeline: ShotTimeline, out_dir: Path, config: PipelineConfig | None = None, *,
                  n_frames: int = DEFAULT_FRAMES, detector: Detector | None = None, embedder: Embedder | None = None,
                  embedder_sha256: str | None = None, params: FitParams | None = None) -> CalibrateOutput:
    cfg = config or PipelineConfig()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    t_all = time.perf_counter()
    analyse = set(cfg.analyze_shot_types)
    intervals = [(s.t0_ms, s.t1_ms) for s in timeline.shots if str(s.type) in analyse]
    if not intervals:
        raise NoWideShots(f"no {sorted(analyse)} shots in the timeline")
    indices = sample_frame_indices(intervals, n_frames, fps=cfg.target_fps)
    if detector is None:
        detector = load_detector(cfg)
    if embedder is None:
        embedder, embedder_sha256 = load_embedder()

    t = time.perf_counter()
    frames = read_frames(normalized, indices, fps=cfg.target_fps)
    t_decode = time.perf_counter() - t
    t = time.perf_counter()
    samples = collect_crops(frames, detector)
    t_detect = time.perf_counter() - t
    t = time.perf_counter()
    emb = embedder([s.crop for s in samples]) if samples else np.zeros((0, 1), np.float32)
    t_embed = time.perf_counter() - t

    t = time.perf_counter()
    try:
        model: TeamModel = fit_team_model(emb, embedder=embedder.name, embedder_sha256=embedder_sha256,
                                          params=params)
    except TeamSeparationFailed as e:
        e.stats.update(frames=len(frames))
        _write_grid(out / SAMPLES_NAME, [("all", [s.crop for s in samples[:64]])])
        log.warning("S3 %s: team separation failed: %s %s", normalized.name, e, e.stats)
        raise
    t_fit = time.perf_counter() - t

    reps = representative(model, emb, n=3)
    model.samples = {str(j): [{"crop": i, "frame_idx": samples[i].frame_idx, "box": list(samples[i].box)}
                              for i in idx] for j, idx in reps.items()}
    pred = model.predict(emb)
    model.stats.update(frames=len(frames), crops_per_frame=round(len(samples) / max(len(frames), 1), 2),
                       outlier_share=round(float((pred == -1).mean()), 4))
    path = model.save(out / TEAM_MODEL_NAME)
    rows = []
    for j, name in ((0, "team 0"), (1, "team 1"), (-1, "no team")):
        idx = np.flatnonzero(pred == j)
        if j >= 0:
            d = model.distances(emb[idx])[:, j]
            idx = idx[np.argsort(d, kind="stable")]  # most typical first
        rows.append((name, [samples[i].crop for i in idx[:16]]))
    grid = _write_grid(out / SAMPLES_NAME, rows)
    timings = {"decode": t_decode, "detect": t_detect, "embed": t_embed, "fit": t_fit,
               "total": time.perf_counter() - t_all}
    log.info("S3 %s: %d frames, %d crops, sizes %s, silhouette %.2f, outliers %.0f%% in %.1fs", normalized.name,
             len(frames), len(samples), model.stats["cluster_sizes"], model.stats["silhouette"],
             100 * model.stats["outlier_share"], timings["total"])
    return CalibrateOutput(team_model=path, samples=grid, frames=len(frames), crops=len(samples),
                           stats=dict(model.stats), timings_s={k: round(v, 3) for k, v in timings.items()})


def _write_grid(path: Path, rows: list[tuple[str, list[np.ndarray]]]) -> Path:
    cv2.imwrite(str(path), crops_grid(rows), [cv2.IMWRITE_JPEG_QUALITY, 90])
    return path
