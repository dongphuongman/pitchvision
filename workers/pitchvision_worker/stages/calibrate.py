"""S3 calibrate (AD §6.2, M1-CV-09): fit the per-video team model from main_wide frames only.

``normalized.mp4`` + S2b ``ShotTimeline`` → ``out_dir/team_model.json`` (portable, no pickle) +
``out_dir/team_samples.jpg`` (crop grid per cluster: for eyeballing and the UI's team confirmation).

1. ``n_frames`` (default 48) frames spread over the ``config.analyze_shot_types`` shots, ≥ 1 s from cuts;
2. D-FINE persons → player candidates (feet on the grass mask, plausible size) → torso crops;
3. SigLIP-base embeddings → team model (spectral split + whitened-LDA predictor, ``cv_core.teams.model``) →
   outlier radius (team -1);
4. ``TeamSeparationFailed`` (→ ``TEAM_SEPARATION_FAILED``) on too few crops, an imbalanced split or a low
   cross-validated Fisher ratio; ``NoWideShots`` (→ ``NO_WIDE_SHOTS``) when the timeline has no analysable footage.

Seeded mode (SCR-14 manual teams, job kind ``team_rerun``): ``seeds = {"0": [{"t_ms", "box": [x1, y1, x2, y2]}, …],
"1": […]}`` — players the user clicked per team. Their torso crops are embedded and ``fit_seeded_team_model`` starts
from the seed centroids (cluster j = the user's team j); the sampled crops still refine it. Without ``seeds`` the
default path is unchanged.

Also written: ``team_candidates.json`` (``{"fps", "frames": [{"frame_idx", "t_ms", "boxes": [[x1, y1, x2, y2], …]}]}``
— the player candidates of the sampled frames: the clickable boxes of SCR-14 manual mode) and the grid layout in
``team_model.json`` ``stats.grid``. On ``TeamSeparationFailed`` the grid + candidates go to ``out_dir/diagnostics/``,
which the executor uploads even though the task fails (``…/models/diagnostics/``).

The task layer uploads both files under ``keys.job_results_prefix(...) + "models/"``.
"""
from __future__ import annotations

import json
import logging
import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from cv_core.teams import (
    Embedder,
    FitParams,
    TeamModel,
    TeamSeparationFailed,
    cluster_colours,
    fit_seeded_team_model,
    fit_team_model,
    representative,
    torso_crop,
)
from cv_core.teams.calibrate import (
    CropSample,
    Detector,
    collect_crops,
    crops_grid,
    read_frames,
    sample_frame_indices,
)
from pitchvision_schemas import PipelineConfig, ShotTimeline

from pitchvision_worker.stages.models import load_detector, load_embedder

log = logging.getLogger(__name__)

TEAM_MODEL_NAME = "team_model.json"
SAMPLES_NAME = "team_samples.jpg"
CANDIDATES_NAME = "team_candidates.json"
DIAGNOSTICS_DIR = "diagnostics"  # uploaded by the executor even when the stage fails
MAX_CANDIDATE_FRAMES = 20
GRID_TILE = (48, 64)       # crops_grid defaults: tile w, h
GRID_LABEL_W = 120
GRID_PER_ROW = 16
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
                  embedder_sha256: str | None = None, params: FitParams | None = None,
                  seeds: Mapping[str, Sequence[Mapping[str, Any]]] | None = None) -> CalibrateOutput:
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
    model: TeamModel
    try:
        if seeds is not None:
            seed_emb, seed_lab = _seed_embeddings(normalized, seeds, embedder, cfg.target_fps)
            base = emb if len(samples) else np.zeros((0, seed_emb.shape[1]), np.float32)
            model = fit_seeded_team_model(base, seed_emb, seed_lab, embedder=embedder.name,
                                          embedder_sha256=embedder_sha256, params=params)
        else:
            model = fit_team_model(emb, embedder=embedder.name, embedder_sha256=embedder_sha256, params=params)
    except TeamSeparationFailed as e:
        e.stats.update(frames=len(frames))
        diag = out / DIAGNOSTICS_DIR
        diag.mkdir(exist_ok=True)
        _write_grid(diag / SAMPLES_NAME, [("all", [s.crop for s in samples[:64]])])
        write_candidates(diag / CANDIDATES_NAME, samples, cfg.target_fps)
        log.warning("S3 %s: team separation failed: %s %s", normalized.name, e, e.stats)
        raise
    t_fit = time.perf_counter() - t

    reps = representative(model, emb, n=3)
    model.samples = {str(j): [{"crop": i, "frame_idx": samples[i].frame_idx, "box": list(samples[i].box)}
                              for i in idx] for j, idx in reps.items()}
    pred = model.predict(emb)
    model.stats.update(frames=len(frames), crops_per_frame=round(len(samples) / max(len(frames), 1), 2),
                       outlier_share=round(float((pred == -1).mean()), 4),
                       colors={str(k): v for k, v in cluster_colours([s.crop for s in samples], pred).items()})
    path = model.save(out / TEAM_MODEL_NAME)
    rows = []
    for j, name in ((0, "team 0"), (1, "team 1"), (-1, "no team")):
        idx = np.flatnonzero(pred == j)
        if j >= 0:
            d = model.distances(emb[idx])[:, j]
            idx = idx[np.argsort(d, kind="stable")]  # most typical first
        rows.append((name, [samples[i].crop for i in idx[:16]]))
    grid = _write_grid(out / SAMPLES_NAME, rows)
    model.stats["grid"] = grid_layout(rows)
    model.save(out / TEAM_MODEL_NAME)  # again, with the grid layout the API turns into sample-crop rectangles
    write_candidates(out / CANDIDATES_NAME, samples, cfg.target_fps)
    timings = {"decode": t_decode, "detect": t_detect, "embed": t_embed, "fit": t_fit,
               "total": time.perf_counter() - t_all}
    log.info("S3 %s: %d frames, %d crops, sizes %s, split %s, cv-Fisher %.1f, outliers %.0f%% in %.1fs",
             normalized.name, len(frames), len(samples), model.stats["cluster_sizes"], model.stats["split"],
             model.stats["cv_fisher"], 100 * model.stats["outlier_share"], timings["total"])
    return CalibrateOutput(team_model=path, samples=grid, frames=len(frames), crops=len(samples),
                           stats=dict(model.stats), timings_s={k: round(v, 3) for k, v in timings.items()})


def grid_layout(rows: Sequence[tuple[str, Sequence[Any]]]) -> dict[str, Any]:
    """Where each crop sits in ``team_samples.jpg`` (crops_grid: one row per entry, label column first)."""
    tw, th = GRID_TILE
    return {"tile_w": tw, "tile_h": th, "label_w": GRID_LABEL_W, "per_row": GRID_PER_ROW,
            "width": GRID_LABEL_W + GRID_PER_ROW * tw, "height": th * max(len(rows), 1),
            "rows": [{"label": label, "cluster": int(label.split()[-1]) if label.startswith("team ") else -1,
                      "count": min(len(crops), GRID_PER_ROW)} for label, crops in rows]}


def write_candidates(path: Path, samples: Sequence[CropSample], fps: float) -> Path:
    """Player candidate boxes of the sampled frames (most populated first, ≤ MAX_CANDIDATE_FRAMES)."""
    by_frame: dict[int, list[list[int]]] = {}
    for smp in samples:
        by_frame.setdefault(smp.frame_idx, []).append([int(v) for v in smp.box])
    top = sorted(by_frame, key=lambda f: (-len(by_frame[f]), f))[:MAX_CANDIDATE_FRAMES]
    frames = [{"frame_idx": f, "t_ms": round(f * 1000 / fps), "boxes": by_frame[f]} for f in sorted(top)]
    path.write_text(json.dumps({"fps": fps, "frames": frames}, separators=(",", ":")))
    return path


def _seed_embeddings(normalized: Path, seeds: Mapping[str, Sequence[Mapping[str, Any]]], embedder: Embedder,
                     fps: float) -> tuple[np.ndarray, np.ndarray]:
    """Torso crops of the user's seed boxes → (embeddings [S, D], team labels [S])."""
    wanted: list[tuple[int, int, tuple[int, int, int, int]]] = []
    for key, picks in seeds.items():
        for pk in picks:
            x1, y1, x2, y2 = (round(float(v)) for v in pk["box"])
            wanted.append((int(key), math.floor(float(pk["t_ms"]) * fps / 1000 + 0.5), (x1, y1, x2, y2)))
    images = dict(read_frames(normalized, sorted({f for _, f, _ in wanted}), fps=fps))
    crops, labels = [], []
    for team, f, box in wanted:
        im = images.get(f)
        c = torso_crop(im, np.asarray(box, np.float32)) if im is not None else None
        if c is not None:
            crops.append(c)
            labels.append(team)
    if not crops:
        raise TeamSeparationFailed("too_few_seeds", "no seed box could be cropped", {"seeds": [0, 0]})
    return np.asarray(embedder(crops)), np.asarray(labels, np.int64)


def _write_grid(path: Path, rows: list[tuple[str, list[np.ndarray]]]) -> Path:
    cv2.imwrite(str(path), crops_grid(rows), [cv2.IMWRITE_JPEG_QUALITY, 90])
    return path
