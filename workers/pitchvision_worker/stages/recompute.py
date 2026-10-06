"""Manual pitch calibration recompute (M1-BE-20, SCR-15; AD §16 "hiệu chỉnh tay 4 điểm → chỉ chạy lại S4 phần chiếu
toạ độ (CPU)"). CPU only: no video decode, no model.

Inputs: the parent job's ``tracks.parquet`` + the ShotTimeline + the user's ≥ 4 point pairs (image pixel on
normalized.mp4 at ``frame_ms`` ↔ ``cv_core.pitch.template`` keypoint index). Output: a new ``tracks.parquet`` with
``px, py, h_quality`` rewritten for the affected frames; every other column is unchanged (same global track ids,
teams, boxes), so S7 tiles / S8 load-db / the manifest run on it exactly as after S5.

- H (image → pitch metres) = ``cv_core.pitch.fit_homography`` (weighted DLT + Gauss-Newton) on the pairs; rejected
  (``HOMOGRAPHY_UNAVAILABLE``, not retryable) when degenerate / non-finite or when the fit is far off
  (reprojection RMSE > ``MAX_RMSE_M``).
- Scope ``shot``: every frame of the analysable shot containing ``frame_ms`` gets the manual H (it overrides any
  automatic H there). Scope ``video``: that shot, plus every frame of the other analysable shots whose automatic H is
  missing or weak (``h_quality`` < 0.5) — good automatic frames are kept.
- Projection = S5's rule (``cv_core.track.stitch.project_rows``): box bottom-centre, NaN behind the camera or more
  than 15 m off the pitch. ``h_quality`` of manual frames = ``1 − RMSE/2 m`` clipped to [0, 1] (4 exact points →
  1.0): it measures the click fit, not the drift below.

LIMITATION (documented in docs/contracts/pipeline-stages.md): broadcast cameras pan and zoom inside a shot, so one H
per shot is only right near ``frame_ms``; positions drift with the camera. cv_core has no frame-to-frame registration
yet (follow-up: propagate H by feature tracking / line alignment between consecutive frames, or a keypoint model —
M1-CV-05). Scope ``video`` is cruder still: it reuses the same H on other shots, which only fits when the camera
framing is similar.
"""
from __future__ import annotations

import logging
import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np
from cv_core.io.parquet import detections_metadata, read_tracks, write_tracks
from cv_core.pitch import FIFA_105x68, PitchTemplate, fit_homography, project
from cv_core.track import FrameHomographies, StitchConfig
from cv_core.track.stitch import project_rows
from pitchvision_schemas import PipelineConfig, ShotTimeline

log = logging.getLogger(__name__)

TRACKS_NAME = "tracks.parquet"
H_QUALITY_OK = 0.5
MAX_RMSE_M = 5.0          # a pair set this inconsistent is a wrong landmark choice, not a usable calibration
MIN_POINTS = 4

Scope = Literal["shot", "video"]


class ManualCalibrationInvalid(Exception):
    """The point pairs don't define a usable homography (job error HOMOGRAPHY_UNAVAILABLE, permanent)."""

    code = "HOMOGRAPHY_UNAVAILABLE"
    retryable = False


@dataclass(frozen=True)
class RecomputeOutput:
    paths: dict[str, Path]
    h: list[list[float]]            # image → pitch (metres)
    rmse_m: float
    scope: Scope
    shot: tuple[int, int]           # [t0_ms, t1_ms) of the calibrated shot
    frames: int                     # frames that got the manual H
    rows: int                       # rows re-projected
    pitch: dict[str, Any]           # {"status": "manual", "homography_ok_pct": …} — same shape as S5's
    stats: dict[str, Any] = field(default_factory=dict)


def manual_homography(points: Sequence[Mapping[str, Any]], template: PitchTemplate = FIFA_105x68
                      ) -> tuple[np.ndarray, float]:
    """(H image→pitch, reprojection RMSE in metres) from ``[{template_index, x, y}, …]``."""
    if len(points) < MIN_POINTS:
        raise ManualCalibrationInvalid(f"{len(points)} point(s) < {MIN_POINTS}")
    idx = [int(p["template_index"]) for p in points]
    if len(set(idx)) != len(idx) or not all(0 <= i < len(template.keypoints) for i in idx):
        raise ManualCalibrationInvalid("template indices must be distinct and within the template")
    src = np.array([[float(p["x"]), float(p["y"])] for p in points])
    dst = template.keypoints[idx]
    try:
        H, _ = fit_homography(src, dst)
    except (np.linalg.LinAlgError, ValueError) as e:
        raise ManualCalibrationInvalid(f"homography fit failed: {e}") from e
    if not np.all(np.isfinite(H)) or abs(float(np.linalg.det(H))) < 1e-12:
        raise ManualCalibrationInvalid("degenerate homography (collinear or repeated points?)")
    proj = project(H, src)
    rmse = float(np.sqrt(np.nanmean(np.sum((proj - dst) ** 2, axis=1))))
    if not math.isfinite(rmse) or rmse > MAX_RMSE_M:
        raise ManualCalibrationInvalid(f"points don't fit one plane view (RMSE {rmse:.1f} m > {MAX_RMSE_M} m)")
    return H / H[2, 2] if abs(H[2, 2]) > 1e-12 else H, rmse


def _frame(ms: float, fps: float) -> int:
    return math.floor(ms * fps / 1000 + 0.5)


def run_recompute(tracks: Path, timeline: ShotTimeline, out_dir: Path, config: PipelineConfig | None = None, *,
                  points: Sequence[Mapping[str, Any]], frame_ms: int, scope: Scope = "shot",
                  template: PitchTemplate = FIFA_105x68) -> RecomputeOutput:
    cfg = config or PipelineConfig()
    t_all = time.perf_counter()
    fps = float(cfg.target_fps)
    analyse = {str(t) for t in cfg.analyze_shot_types}
    shot = next((s for s in timeline.shots if s.t0_ms <= frame_ms < s.t1_ms), None)
    if shot is None or str(shot.type) not in analyse:
        raise ManualCalibrationInvalid(f"frame {frame_ms} ms is not inside an analysed shot")
    H, rmse = manual_homography(points, template)
    q_manual = float(np.clip(1.0 - rmse / 2.0, 0.0, 1.0))

    table = read_tracks(tracks)
    cols = {c: table.column(c).to_numpy(zero_copy_only=False).copy() for c in table.column_names}
    f = cols["frame_idx"].astype(np.int64)
    hq_old = cols["h_quality"].astype(np.float32)

    wide = [(_frame(s.t0_ms, fps), _frame(s.t1_ms, fps)) for s in timeline.shots if str(s.type) in analyse]
    a, b = _frame(shot.t0_ms, fps), _frame(shot.t1_ms, fps)
    in_shot = (f >= a) & (f < b)
    target = in_shot.copy()
    manual_frames = set(range(a, b))
    if scope == "video":
        weak = ~(np.nan_to_num(hq_old, nan=-1.0) >= H_QUALITY_OK)
        in_wide = np.zeros(len(f), bool)
        for lo, hi in wide:
            in_wide |= (f >= lo) & (f < hi)
        target |= in_wide & weak
        good = {int(x) for x in np.unique(f[in_wide & ~weak])}
        for lo, hi in wide:
            manual_frames.update(x for x in range(lo, hi) if x not in good)

    if target.any():
        sel = {k: v[target] for k, v in cols.items()}
        uniq = np.unique(sel["frame_idx"].astype(np.int64))
        hom = FrameHomographies(uniq.astype(np.int64), np.repeat(H[None], len(uniq), axis=0),
                                np.full(len(uniq), q_manual, np.float32))
        px, py, hq = project_rows(sel, hom, StitchConfig(fps=fps, pitch_size_m=(template.length, template.width)))
        cols["px"][target] = px
        cols["py"][target] = py
        cols["h_quality"][target] = hq

    # pitch verdict + coverage on main_wide frames (frames without rows: manual H if in scope, else unknown = weak)
    per_frame: dict[int, float] = {}
    hq_new = cols["h_quality"].astype(np.float32)
    for fi, q in zip(f.tolist(), hq_new.tolist(), strict=True):
        if math.isfinite(q):
            per_frame[fi] = max(per_frame.get(fi, -1.0), q)
    total = ok = 0
    for s in timeline.shots:
        if str(s.type) != "main_wide":
            continue
        for fi in range(_frame(s.t0_ms, fps), _frame(s.t1_ms, fps)):
            total += 1
            q = q_manual if fi in manual_frames else per_frame.get(fi, -1.0)
            ok += int(q >= H_QUALITY_OK)
    pct = round(100.0 * ok / total, 2) if total else 0.0

    old_meta = detections_metadata(table).get("meta") or {}
    meta = {**old_meta, "stage": "recompute", "manual_calibration": {"h": H.tolist(), "rmse_m": round(rmse, 4),
            "scope": scope, "frame_ms": frame_ms, "shot": [shot.t0_ms, shot.t1_ms]}}  # fmt: skip
    path = write_tracks(Path(out_dir) / TRACKS_NAME, cols, meta)
    rows = int(target.sum())
    log.info("recompute: manual H (RMSE %.2f m, scope %s) → %d rows in %d frame(s); homography_ok %.1f%%",
             rmse, scope, rows, len(manual_frames), pct)
    return RecomputeOutput(paths={"tracks": path}, h=H.tolist(), rmse_m=round(rmse, 4), scope=scope,
                           shot=(shot.t0_ms, shot.t1_ms), frames=len(manual_frames), rows=rows,
                           pitch={"status": "manual", "homography_ok_pct": pct, "main_wide_frames": total},
                           stats={"elapsed_s": round(time.perf_counter() - t_all, 3),
                                  "projected_rows": int(np.isfinite(cols["px"][target]).sum())})
