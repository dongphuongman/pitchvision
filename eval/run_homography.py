"""Homography eval (M1-CV-05 / M1-CV-18): line-alignment h_quality on golden frames and, where GT pitch keypoints
exist, metric errors in pitch metres. Overall, per match and per stratum.

Predictions come from the cv_core pipeline (``--model``: keypoint ONNX → ``estimate_homography``) or from a
``pv-homographies/v1`` file (``--pred``). Frames are decoded from the raw golden videos on the normalised timeline
(``--long-side``, default 1280 like S2). Frames evaluated = the GT frames (``--labels``), or the SP-09 main_wide
frames (``--sp09``: no GT → line alignment only).

Metrics per frame:
- ``h_quality``  line alignment of the projected FIFA template with detected white lines (cv_core.pitch.lines);
  frames without a homography count as 0;
- ``kp_err``     (GT) error in metres of the predicted H at each labelled keypoint (projected GT pixel vs template);
- ``field_err``  (GT, ≥ 4 well-spread labelled points) mean metre error vs the GT homography over a pixel grid of
  the visible pitch — what a player's foot anywhere on screen gets. M1-CV-18 target: ≥ 85 % of main_wide frames
  with field_err < 1 m (frames without H count as failures).

    uv run python eval/run_homography.py --model ~/.cache/pitchvision/models/pitch-kp-yolo11m-pv-v1.onnx --sp09 \
        --render-dir /tmp/pv_h --out eval/out/homography_sp09.json
    uv run python eval/run_homography.py --pred out/h.json --labels labels/pitch_kp.json
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from pveval.formats import (
    HomographyPred,
    PitchLabel,
    ShotSample,
    read_homographies,
    read_pitch_labels,
    write_homographies,
)
from pveval.golden import GOLDEN_JSON, REPO, STRATA, default_golden_dir, load_golden
from pveval.metrics import WIDE, by_group, field_errors, homography_report, quality_report, reprojection_errors
from pveval.report import group_tables, table
from pveval.shots import frames_from_videos, sp09_samples

GOOD_Q = 0.7  # h_quality at/above which a frame counts as "aligned" in the summary columns


@dataclass
class FrameResult:
    video: str
    t_ms: int
    size: tuple[int, int]
    H: np.ndarray | None
    quality: float | None
    precision: float = float("nan")
    recall: float = float("nan")
    reason: str = ""
    kp_err: list[float] = field(default_factory=list)
    field_err: float | None = None   # None = no GT homography for this frame
    has_gt: bool = False


def _scale_H(H: list[list[float]], from_size: tuple[int, int], to_size: tuple[int, int]) -> np.ndarray:
    sx, sy = from_size[0] / to_size[0], from_size[1] / to_size[1]
    return np.asarray(H, float) @ np.diag([sx, sy, 1.0])


def gt_homography(label: PitchLabel, size: tuple[int, int]) -> np.ndarray | None:
    """image→pitch from ≥ 4 labelled points that are not collinear on the pitch (plain weighted DLT, no RANSAC)."""
    from cv_core.pitch import PitchTemplate, fit_homography

    pts = label.scaled(*size)
    if len(pts) < 4:
        return None
    tpl = PitchTemplate(*label.pitch).keypoints
    idx = sorted(pts)
    src = tpl[idx]
    if np.linalg.svd(src - src.mean(0), compute_uv=False)[1] / math.sqrt(len(idx)) < 1.5:
        return None
    H_p2i, _ = fit_homography(src, np.array([pts[i] for i in idx]))
    H = np.linalg.inv(H_p2i)
    return H / H[2, 2]


def _render(img: np.ndarray, r: FrameResult, kp: Any, est: Any, label: PitchLabel | None, path: Path,
            template: Any) -> None:
    from cv_core.pitch import render_lines

    out = img.copy()
    if r.H is not None:
        out = render_lines(np.linalg.inv(r.H), r.size, template, thickness=2, image=out, colour=(255, 0, 255))
    if kp is not None:
        for i, ((x, y), c) in enumerate(zip(kp.xy, kp.conf, strict=True)):
            if c < 0.3:
                continue
            col = (0, 220, 255)  # confident but not used (border / below min_conf)
            if est is not None and est.used[i]:
                col = (0, 255, 0) if est.inliers[i] else (0, 0, 255)
            cv2.circle(out, (int(x), int(y)), 6, col, 2)
            cv2.putText(out, str(i), (int(x) + 7, int(y) - 7), cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 1)
    if label is not None:
        for (x, y) in label.scaled(*r.size).values():
            cv2.drawMarker(out, (int(x), int(y)), (255, 200, 0), cv2.MARKER_CROSS, 14, 2)
    q = "-" if r.quality is None else f"{r.quality:.2f}"
    txt = f"{r.video} t={r.t_ms / 1000:.0f}s q={q} P={r.precision:.2f} R={r.recall:.2f} {r.reason}"
    cv2.rectangle(out, (0, 0), (out.shape[1], 30), (0, 0, 0), -1)
    cv2.putText(out, txt, (8, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.imwrite(str(path), out, [cv2.IMWRITE_JPEG_QUALITY, 85])


def evaluate(keys: list[tuple[str, int]], golden_dir: Path, long_side: int, *, model_path: Path | None,
             provider: str, preds: dict[tuple[str, int], HomographyPred] | None,
             labels: dict[tuple[str, int], PitchLabel], pitch: tuple[float, float],
             render_dir: Path | None) -> list[FrameResult]:
    from cv_core.pitch import (
        PitchKeypointModel,
        PitchTemplate,
        estimate_homography,
        line_alignment,
        line_mask,
    )

    template = PitchTemplate(*pitch)
    kp_model = None
    if model_path is not None:
        from cv_core.runtime import Runtime

        kp_model = PitchKeypointModel(Runtime(provider, models={"kp": model_path}).model("kp"))
    results = []
    samples = [ShotSample(v, t, WIDE) for v, t in keys]
    for s, img in frames_from_videos(golden_dir, samples, long_side=long_side):
        key = (s.video, s.t_ms)
        label = labels.get(key)
        kp = est = None
        if img is None:
            p = preds.get(key) if preds else None
            if p is None:
                continue  # neither frame nor prediction: cannot evaluate
            size = (p.width, p.height)
            H = np.asarray(p.H, float) if p.H is not None else None
            r = FrameResult(s.video, s.t_ms, size, H, p.h_quality, reason=p.reason)
        else:
            size = (img.shape[1], img.shape[0])
            if kp_model is not None:
                kp = kp_model([img])[0]
                est = estimate_homography(kp, template)
                H, reason = est.H, est.reason
            else:
                p = preds.get(key) if preds else None
                H = _scale_H(p.H, (p.width, p.height), size) if p is not None and p.H is not None else None
                reason = (p.reason or "ok") if p is not None else "no prediction"
            al = line_alignment(line_mask(img), np.linalg.inv(H) if H is not None else None, template)
            r = FrameResult(s.video, s.t_ms, size, H, al.quality if H is not None else None,
                            al.precision, al.recall, reason)
        if label is not None:
            r.has_gt = True
            gpts = label.scaled(*size)
            gtpl = PitchTemplate(*label.pitch).keypoints
            if r.H is not None and gpts:
                idx = sorted(gpts)
                r.kp_err = reprojection_errors(r.H, np.array([gpts[i] for i in idx]), gtpl[idx]).tolist()
            H_gt = gt_homography(label, size)
            if H_gt is not None:
                if r.H is None:
                    r.field_err = math.inf  # no homography on a frame that has one = failure
                else:
                    fe = field_errors(r.H, H_gt, size, pitch=label.pitch)
                    r.field_err = float(fe.mean()) if fe.size else None
        if render_dir is not None and img is not None:
            name = f"{s.video}_{s.t_ms // 1000:04d}_q{0 if r.quality is None else r.quality:.2f}.jpg"
            _render(img, r, kp, est, label, render_dir / name, template)
        results.append(r)
    return results


def _row(rs: list[FrameResult]) -> dict[str, float]:
    qr = quality_report([r.quality for r in rs])
    row: dict[str, float] = {"n": len(rs), "with_H": qr.n_with_h / qr.n if qr.n else float("nan"),
                             "q_mean": qr.mean, "q_median": qr.median, f"q>={GOOD_Q}": qr.share_at_least[GOOD_Q]}
    kp = [e for r in rs for e in r.kp_err]
    if any(r.has_gt for r in rs):
        row["kp_err_cm_med"] = float(np.median(kp)) * 100 if kp else float("nan")
        fe = [r.field_err for r in rs if r.field_err is not None or (r.has_gt and r.H is None)]
        rep = homography_report([None if (e is None or math.isinf(e)) else e for e in fe], threshold=1.0)
        row["field_err_cm_med"] = rep.median_error * 100
        row["field<1m"] = rep.pct_within
    return row


def _outside_repo(p: Path) -> Path:
    p = p.expanduser().resolve()
    if p == REPO or REPO in p.parents:
        raise SystemExit(f"--render-dir {p} is inside the repo: rendered broadcast frames must never be committed")
    return p


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--model", type=Path, help="pitch keypoint ONNX (cv_core.pitch.PitchKeypointModel)")
    src.add_argument("--pred", type=Path, help="pv-homographies/v1 predictions")
    ap.add_argument("--labels", type=Path, help="pv-pitch-keypoints/v1 GT; evaluated frames = labelled frames")
    ap.add_argument("--sp09", action="store_true", help="evaluate the SP-09 main_wide frames")
    ap.add_argument("--sp09-dir", type=Path, default=REPO / "spikes" / "results" / "sp09")
    ap.add_argument("--golden-dir", type=Path, default=None)
    ap.add_argument("--golden", type=Path, default=GOLDEN_JSON)
    ap.add_argument("--long-side", type=int, default=1280)
    ap.add_argument("--provider", default="ort_cpu", help="ort_cpu | ort_coreml | ort_cuda | tensorrt")
    ap.add_argument("--pitch", type=float, nargs=2, default=(105.0, 68.0), metavar=("L", "W"))
    ap.add_argument("--videos", nargs="*", help="restrict to these video ids")
    ap.add_argument("--limit", type=int, default=0, help="max frames per video (0 = all)")
    ap.add_argument("--render-dir", type=Path, help="write overlays here (must be OUTSIDE the repo)")
    ap.add_argument("--write-pred", type=Path, help="save predictions as pv-homographies/v1")
    ap.add_argument("--out", type=Path, help="JSON report")
    a = ap.parse_args()

    labels = {(lb.video, lb.t_ms): lb for lb in read_pitch_labels(a.labels)} if a.labels else {}
    if a.sp09:
        samples, _ = sp09_samples(a.sp09_dir)
        keys = [(s.video, s.t_ms) for s in samples if s.label == WIDE]
    elif labels:
        keys = sorted(labels)
    else:
        raise SystemExit("give --labels and/or --sp09")
    if a.videos:
        keys = [k for k in keys if k[0] in set(a.videos)]
    if a.limit:
        per: Counter[str] = Counter()
        kept = []
        for k in keys:
            if per[k[0]] < a.limit:
                kept.append(k)
                per[k[0]] += 1
        keys = kept
    render_dir = _outside_repo(a.render_dir) if a.render_dir else None
    if render_dir:
        render_dir.mkdir(parents=True, exist_ok=True)
    preds = read_homographies(a.pred) if a.pred else None
    golden_dir = a.golden_dir or default_golden_dir()
    res = evaluate(keys, golden_dir, a.long_side, model_path=a.model, provider=a.provider, preds=preds,
                   labels=labels, pitch=tuple(a.pitch), render_dir=render_dir)  # type: ignore[arg-type]
    if not res:
        raise SystemExit(f"nothing evaluated (no frames in {golden_dir} and no predictions)")

    by_video: dict[str, list[FrameResult]] = {}
    for r in res:
        by_video.setdefault(r.video, []).append(r)
    rows = {v: _row(rs) for v, rs in sorted(by_video.items())}
    cols = list(next(iter(rows.values())).keys())
    print(table("per match", rows, cols))
    golden = load_golden(a.golden)
    strata = {v: golden.get(v, {}) for v in by_video}
    tables = by_group(strata, STRATA, lambda ids: _row([r for v in ids for r in by_video[v]]))
    print("\n" + group_tables(tables, cols))
    overall = _row(res)
    print("\n" + table("ALL", {"all": overall}, cols))
    qr = quality_report([r.quality for r in res])
    print(f"\nh_quality histogram (0.1 bins, no-H = 0): {qr.histogram}")
    print("share ≥ " + ", ".join(f"{t}: {v:.1%}" for t, v in qr.share_at_least.items()))
    reasons = Counter(r.reason.split(" (")[0] for r in res if r.H is None)
    if reasons:
        print("no homography:", dict(reasons.most_common()))
    if a.write_pred:
        write_homographies(a.write_pred, [HomographyPred(r.video, r.t_ms, r.size[0], r.size[1],
                                                         r.H.tolist() if r.H is not None else None, r.quality,
                                                         r.reason) for r in res],
                           source=str(a.model or a.pred))
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps({
            "overall": overall, "per_match": rows, "by_group": {t.key: t.rows for t in tables},
            "quality": {"histogram": qr.histogram, "share_at_least": {str(k): v for k, v in qr.share_at_least.items()},
                        "mean": qr.mean, "median": qr.median, "n": qr.n, "n_with_h": qr.n_with_h},
            "no_homography_reasons": dict(reasons),
        }, indent=1))


if __name__ == "__main__":
    main()
