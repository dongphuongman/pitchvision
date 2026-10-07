"""Manual pitch calibration recompute (M1-BE-20, SCR-15; AD §16 "hiệu chỉnh tay 4 điểm → chỉ chạy lại S4 phần chiếu
toạ độ (CPU)"). CPU only, no model; decodes only the calibrated shot of ``normalized.mp4`` (camera tracking).

Inputs: the parent job's ``tracks.parquet`` + the ShotTimeline + the user's ≥ 4 point pairs (image pixel on
normalized.mp4 at ``frame_ms`` ↔ ``cv_core.pitch.template`` keypoint index). Output: a new ``tracks.parquet`` with
``px, py, h_quality`` rewritten for the affected frames; every other column is unchanged (same global track ids,
teams, boxes), so S7 tiles / S8 load-db / the manifest run on it exactly as after S5.

- H (image → pitch metres) = ``cv_core.pitch.fit_homography`` (weighted DLT + Gauss-Newton) on the pairs; rejected
  (``HOMOGRAPHY_UNAVAILABLE``, not retryable) when degenerate / non-finite or when the fit is far off
  (reprojection RMSE > ``MAX_RMSE_M``).
- Scope ``shot``: every frame of the analysable shot containing ``frame_ms`` gets the manual H (it overrides any
  automatic H there), moved with the camera: ``cv_core.pitch.motion.CameraMotion`` estimates the frame-to-frame
  image motion over the shot and ``propagate`` chains the clicked frame's H forwards and backwards
  (``h_quality`` = the click quality minus ``MANUAL_DECAY`` per chained frame). Frames the chain can't reach
  (motion lost: blur, a graphic covering the frame) keep the fixed manual H with ``h_quality`` ≤ ``UNTRACKED_Q``.
  Without ``normalized`` (or with ``track_camera=False``) the whole shot gets the fixed H, as before.
- Scope ``video``: that shot, plus every frame of the other analysable shots whose automatic H is
  missing or weak (``h_quality`` < 0.5) — good automatic frames are kept.
- Projection = S5's rule (``cv_core.track.stitch.project_rows``): box bottom-centre, NaN behind the camera or more
  than 15 m off the pitch. ``h_quality`` of manual frames = ``1 − RMSE/2 m`` clipped to [0, 1] (4 exact points →
  1.0) on the clicked frame, then decaying along the camera chain (above).

LIMITATION (documented in docs/contracts/pipeline-stages.md): chained motion drifts slowly (no line re-alignment yet,
M1-CV-20), so frames far from ``frame_ms`` are less exact. Scope ``video`` reuses the fixed H on the OTHER shots
(no clicked frame there to track from), which only fits when the camera framing is similar.
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
from cv_core.io.video import VideoReader
from cv_core.pitch import FIFA_105x68, PitchTemplate, fit_homography, project
from cv_core.pitch.motion import CameraMotion, propagate
from cv_core.track import FrameHomographies, StitchConfig
from cv_core.track.stitch import project_rows
from pitchvision_schemas import PipelineConfig, ShotTimeline

log = logging.getLogger(__name__)

TRACKS_NAME = "tracks.parquet"
H_QUALITY_OK = 0.5
MAX_RMSE_M = 5.0          # a pair set this inconsistent is a wrong landmark choice, not a usable calibration
MIN_POINTS = 4
MANUAL_DECAY = 0.002      # per chained frame: a perfect click stays ≥ 0.5 for 10 s of camera motion at 25 fps
UNTRACKED_Q = 0.3         # fixed H on a frame the camera chain can't reach: shown, but counted as weak

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


def track_manual_h(normalized: Path, a: int, b: int, key: int, H: np.ndarray, q: float, fps: float
                   ) -> tuple[np.ndarray, np.ndarray, int]:  # fmt: skip
    """Per-frame H / quality for frames ``[a, b)`` from the clicked frame ``key``'s ``H`` moved with the camera.
    → (H [b-a, 3, 3], quality [b-a], frames reached by the chain)."""
    n = b - a
    reader = VideoReader(normalized, a / fps, b / fps, stride=1, target_fps=fps)
    camera = CameraMotion()
    motion: list[np.ndarray | None] = [None] * n
    seen = np.zeros(n, bool)
    last = -1
    for fr in reader:
        k = fr.idx - a
        if not 0 <= k < n:
            continue
        if k != last + 1:
            camera.reset()  # a gap in the decoded frames: don't chain across it
        motion[k], seen[k], last = camera.step(fr.image), True, k
    Hs = np.repeat(H[None], n, axis=0)
    qs = np.full(n, min(q, UNTRACKED_Q), np.float32)
    k0 = min(max(key - a, 0), n - 1)
    qs[k0] = q
    prop, pq = propagate(n, {k0: (H, q)}, motion, set(), image_size=reader.size, max_gap=n, decay=MANUAL_DECAY)
    tracked = 0
    for k, Hk in enumerate(prop):
        if Hk is not None and seen[k]:
            Hs[k], qs[k] = Hk, pq[k]
            tracked += 1
    return Hs, qs, tracked


def run_recompute(tracks: Path, timeline: ShotTimeline, out_dir: Path, config: PipelineConfig | None = None, *,
                  points: Sequence[Mapping[str, Any]], frame_ms: int, scope: Scope = "shot",
                  template: PitchTemplate = FIFA_105x68, normalized: Path | None = None,
                  track_camera: bool = True) -> RecomputeOutput:
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
    # per-frame manual H over the calibrated shot (camera-tracked when the video is at hand)
    shot_H, shot_q, tracked = np.repeat(H[None], b - a, axis=0), np.full(b - a, q_manual, np.float32), 0
    if normalized is not None and track_camera and b > a:
        try:
            shot_H, shot_q, tracked = track_manual_h(normalized, a, b, _frame(frame_ms, fps), H, q_manual, fps)
        except Exception as e:  # tracking refines the fixed H: an unreadable video must not fail the job
            log.warning("recompute: camera tracking unavailable (%r); fixed manual H on the shot", e)
    manual_q = {a + k: float(shot_q[k]) for k in range(b - a)}
    if scope == "video":
        weak = ~(np.nan_to_num(hq_old, nan=-1.0) >= H_QUALITY_OK)
        in_wide = np.zeros(len(f), bool)
        for lo, hi in wide:
            in_wide |= (f >= lo) & (f < hi)
        target |= in_wide & weak
        good = {int(x) for x in np.unique(f[in_wide & ~weak])}
        for lo, hi in wide:
            manual_q.update((x, q_manual) for x in range(lo, hi) if x not in good and x not in manual_q)

    if target.any():
        sel = {k: v[target] for k, v in cols.items()}
        uniq = np.unique(sel["frame_idx"].astype(np.int64))
        in_cal = (uniq >= a) & (uniq < b)
        Hu = np.repeat(H[None], len(uniq), axis=0)
        qu = np.full(len(uniq), q_manual, np.float32)
        Hu[in_cal], qu[in_cal] = shot_H[uniq[in_cal] - a], shot_q[uniq[in_cal] - a]
        hom = FrameHomographies(uniq.astype(np.int64), Hu, qu)
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
            q = manual_q[fi] if fi in manual_q else per_frame.get(fi, -1.0)
            ok += int(q >= H_QUALITY_OK)
    pct = round(100.0 * ok / total, 2) if total else 0.0

    old_meta = detections_metadata(table).get("meta") or {}
    meta = {**old_meta, "stage": "recompute", "manual_calibration": {"h": H.tolist(), "rmse_m": round(rmse, 4),
            "scope": scope, "frame_ms": frame_ms, "shot": [shot.t0_ms, shot.t1_ms],
            "camera_tracked_frames": tracked}}  # fmt: skip
    path = write_tracks(Path(out_dir) / TRACKS_NAME, cols, meta)
    rows = int(target.sum())
    log.info("recompute: manual H (RMSE %.2f m, scope %s, camera tracked on %d/%d) → %d rows in %d frame(s); "
             "homography_ok %.1f%%", rmse, scope, tracked, b - a, rows, len(manual_q), pct)
    return RecomputeOutput(paths={"tracks": path}, h=H.tolist(), rmse_m=round(rmse, 4), scope=scope,
                           shot=(shot.t0_ms, shot.t1_ms), frames=len(manual_q), rows=rows,
                           pitch={"status": "manual", "homography_ok_pct": pct, "main_wide_frames": total},
                           stats={"elapsed_s": round(time.perf_counter() - t_all, 3),
                                  "projected_rows": int(np.isfinite(cols["px"][target]).sum()),
                                  "camera_tracked_frames": tracked, "shot_frames": b - a})
