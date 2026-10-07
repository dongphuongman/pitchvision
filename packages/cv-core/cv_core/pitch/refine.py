"""Homography refinement by line alignment (M1-CV-20): pull the projected pitch markings onto the white lines.

Keypoints (model or clicks) leave a few-pixel error that grows with distance from them; chaining camera motion drifts
slowly. Both are fixed the same way: minimise the truncated chamfer distance between the template markings projected
through ``H_pitch→image`` and the frame's white-line mask (``cv_core.pitch.lines.line_mask``).

- Samples: every marking sampled every ``step_m``, projected; kept when in front of the camera, inside the frame
  (minus the unobservable border) and on visible grass. A marking with fewer than ``min_marking_samples`` observable
  samples is dropped (a far stub the line mask can't keep); the others weigh √n in total, so the long touchlines
  can't pin a shift along themselves and a few stray samples can't dominate.
- Residual: distance-transform value of the white mask (Gaussian-smoothed so it has a minimum on the line's centre,
  not a flat zero across its painted width) at the projected sample, truncated at ``tau`` px; its gradient points
  away from the nearest line, so this is a point-to-LINE distance (no sliding along a line).
- Update: ``H ← N⁻¹ (I + A) N H`` with the 8 entries of ``A`` (``A₂₂ = 0``) solved by damped Gauss-Newton in
  normalised image coordinates, backtracking until the truncated cost drops; coarse-to-fine ``tau`` (``taus``) so a
  10–20 px error is caught first.
- Guard: the result is accepted only if ``line_alignment`` quality (the ``h_quality`` metric, SP-03) rises by at least
  ``min_gain``; otherwise the input H comes back unchanged (``accepted=False``). Refinement can't invent lines that
  aren't there, so a frame with too few observable markings is left alone.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from cv_core.pitch.lines import LineMask, line_alignment
from cv_core.pitch.template import FIFA_105x68, PitchTemplate


@dataclass(frozen=True, slots=True)
class Refinement:
    H_p2i: np.ndarray        # pitch → image (the input when not accepted)
    quality_before: float
    quality: float           # line-alignment quality of ``H_p2i``
    accepted: bool
    iterations: int
    samples: int             # observable template samples used in the last pass


def _samples(template: PitchTemplate, step_m: float) -> tuple[np.ndarray, np.ndarray]:
    pts, ids = [], []
    for i, line in enumerate(template.lines(step_m)):
        if line.kind == "spot":
            continue
        pts.append(line.points)
        ids.append(np.full(len(line.points), i))
    return np.concatenate(pts).astype(np.float64), np.concatenate(ids)


def _bilinear(img: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    h, w = img.shape
    x0 = np.clip(np.floor(x).astype(np.int64), 0, w - 2)
    y0 = np.clip(np.floor(y).astype(np.int64), 0, h - 2)
    fx, fy = np.clip(x - x0, 0, 1), np.clip(y - y0, 0, 1)
    top = img[y0, x0] * (1 - fx) + img[y0, x0 + 1] * fx
    bot = img[y0 + 1, x0] * (1 - fx) + img[y0 + 1, x0 + 1] * fx
    return np.asarray(top * (1 - fy) + bot * fy)


def refine_homography(mask: LineMask, H_p2i: np.ndarray, template: PitchTemplate = FIFA_105x68, *,
                      taus: tuple[float, ...] = (24.0, 10.0, 5.0, 3.0), iters: int = 8, step_m: float = 0.5,
                      min_samples: int = 80, min_gain: float = 0.005, damping: float = 1e-3,
                      smooth_px: float = 2.0, min_marking_samples: int = 10) -> Refinement:
    """Refine ``H_p2i`` (pitch → image) against ``mask``; see the module docstring. Distances in px at the mask's
    resolution; ``taus`` are tuned at 1280 px wide and scale with the frame width."""
    w, h = mask.size
    H0 = np.asarray(H_p2i, np.float64)
    before = line_alignment(mask, H0, template).quality
    if not np.all(np.isfinite(H0)):
        return Refinement(H0, before, before, False, 0, 0)
    k = w / 1280.0
    border = round(0.008 * w)
    dist = cv2.distanceTransform((~mask.white).astype(np.uint8), cv2.DIST_L2, 5).astype(np.float32)
    # the distance is 0 across the whole painted width: smoothing gives it a minimum on the line's centre
    if smooth_px > 0:
        dist = np.asarray(cv2.GaussianBlur(dist, (0, 0), smooth_px * k), np.float32)
    gx = cv2.Sobel(dist, cv2.CV_32F, 1, 0, ksize=3) / 8.0
    gy = cv2.Sobel(dist, cv2.CV_32F, 0, 1, ksize=3) / 8.0
    P, ids = _samples(template, step_m)
    Ph = np.c_[P, np.ones(len(P))]
    s = w / 2.0
    N = np.array([[1 / s, 0, -w / (2 * s)], [0, 1 / s, -h / (2 * s)], [0, 0, 1]])
    Ni = np.linalg.inv(N)
    Hn = N @ H0
    sign = 1.0 if float((Hn @ np.r_[template.keypoints.mean(0), 1.0])[2]) > 0 else -1.0
    n_mark = int(ids.max()) + 1

    def observe(Hn: np.ndarray, tau: float) -> tuple[float, dict[str, np.ndarray]] | None:
        """(cost, state) at ``Hn``: cost = per-marking-weighted mean of min(D, tau)² over the observable samples."""
        q = Ph @ Hn.T
        with np.errstate(divide="ignore", invalid="ignore"):
            xp, yp = q[:, 0] / q[:, 2] * s + w / 2, q[:, 1] / q[:, 2] * s + h / 2
        ok = (q[:, 2] * sign > 1e-9) & np.isfinite(xp) & np.isfinite(yp)
        ok &= (xp >= border) & (xp < w - border) & (yp >= border) & (yp < h - border)
        idx = np.flatnonzero(ok)
        idx = idx[mask.grass[np.round(yp[idx]).astype(np.int64), np.round(xp[idx]).astype(np.int64)]]
        if len(idx) < min_samples:
            return None
        per = np.bincount(ids[idx], minlength=n_mark).astype(np.float64)
        idx = idx[per[ids[idx]] >= min_marking_samples]  # a far-away stub: too short for the line mask to keep
        if len(idx) < min_samples:
            return None
        x, y = xp[idx], yp[idx]
        d = _bilinear(dist, x, y)
        wt = 1.0 / np.sqrt(per[ids[idx]])  # a marking weighs √n: short markings count, the touchline can't dominate
        cost = float((wt * np.minimum(d, tau) ** 2).sum() / wt.sum())
        return cost, {"idx": idx, "x": x, "y": y, "d": d, "wt": wt, "q": q[idx] / q[idx, 2:3]}

    def apply(Hn: np.ndarray, delta: np.ndarray) -> np.ndarray:
        A = np.array([[delta[0], delta[1], delta[2]], [delta[3], delta[4], delta[5]], [delta[6], delta[7], 0.0]])
        return (np.eye(3) + A) @ Hn

    it = n_used = 0
    for tau in (t * k for t in taus):
        cur = observe(Hn, tau)
        for _ in range(iters):
            if cur is None:
                break
            cost, st = cur
            near = st["d"] < tau  # beyond tau: truncated (constant residual, no gradient) → no influence
            if near.sum() < min_samples:
                break
            x, y, d, wt, p = st["x"][near], st["y"][near], st["d"][near], st["wt"][near], st["q"][near]
            n_used = len(x)
            # residual r = D(x(A), y(A)); dr/dA via the image gradient of D and the projection's Jacobian at A = 0
            dx, dy = _bilinear(gx, x, y) * s, _bilinear(gy, x, y) * s
            xs, ys = p[:, 0], p[:, 1]
            J = np.stack([dx * xs, dx * ys, dx, dy * xs, dy * ys, dy,
                          -(dx * xs + dy * ys) * xs, -(dx * xs + dy * ys) * ys], axis=1)  # fmt: skip
            JtW = J.T * wt
            M = JtW @ J
            M += damping * np.trace(M) / 8 * np.eye(8)
            delta = -np.linalg.solve(M, JtW @ d)
            it += 1
            for _half in range(6):  # backtracking: only steps that lower the truncated cost
                cand = apply(Hn, delta)
                nxt = observe(cand, tau)
                if nxt is not None and nxt[0] < cost:
                    break
                delta = delta / 2
            else:
                break  # no descent: converged at this tau
            Hn, cur = cand, nxt
            if cost - nxt[0] < 1e-4 * max(cost, 1e-9):
                break
    H1 = Ni @ Hn
    H1 = H1 / H1[2, 2] if abs(H1[2, 2]) > 1e-12 else H1
    after = line_alignment(mask, H1, template).quality if np.all(np.isfinite(H1)) else 0.0
    if after >= before + min_gain:
        return Refinement(H1, before, after, True, it, n_used)
    return Refinement(H0, before, before, False, it, n_used)
