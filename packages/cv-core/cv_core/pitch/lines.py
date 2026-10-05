"""Line alignment: how well a homography's projected pitch markings agree with the white lines in the frame.

This is ``h_quality`` (AD §6.5). It is **independent of the keypoints** that produced H — SP-03 showed that the
residual on the fitted keypoints says ~nothing (86 % "success" on overlays that were visibly wrong).

1. ``line_mask``: grass = HSV green, morphologically closed so painted lines inside it count as grass;
   white line pixels = grey top-hat (bright thin structures) ≥ ``min_contrast`` inside the grass, minus thick
   blobs (anything surviving an opening wider than a painted line: light shirts, shorts, logos), keeping only
   elongated connected components (socks, the ball, small fragments go). A band of ``border`` px along the frame
   edge is not observable (the top-hat cannot see a line cut by the edge).
2. ``render_lines``: project every template marking (``PitchTemplate.lines``) through ``H_pitch→image``,
   1-px polylines, cut where a sample falls behind the camera.
3. precision ``P`` = soft share of rendered template pixels on grass that lie within ``tol`` px of a white pixel
   (distance transform of the white mask), averaged per marking; recall ``R`` = soft share of white pixels within
   ``tol`` px of a rendered line. Soft score = 1 up to ``tol``, linear to 0 at ``3·tol``.
4. ``quality`` = F-beta(P, R) with β = 0.5 (precision counts more: line-like clutter — logos, mowing seams, shadows
   — lowers R for a correct H). 0 when fewer than ``min_template_px`` template pixels are observable: we cannot
   verify an H that projects (almost) no markings onto visible grass.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from cv_core.pitch.template import FIFA_105x68, PitchTemplate

GRASS_HSV_LO = (30, 40, 40)
GRASS_HSV_HI = (90, 255, 255)


@dataclass(frozen=True, slots=True)
class LineMask:
    white: np.ndarray  # bool HxW: line pixels
    grass: np.ndarray  # bool HxW: grass incl. the lines painted on it

    @property
    def size(self) -> tuple[int, int]:
        return self.white.shape[1], self.white.shape[0]


def _odd(n: float) -> int:
    k = max(3, round(n))
    return k if k % 2 else k + 1


def line_mask(frame: np.ndarray, *, min_contrast: float = 22.0, min_len_frac: float = 0.025) -> LineMask:
    """BGR uint8 frame → ``LineMask``. Kernel sizes scale with the frame width (tuned at 1280 px)."""
    w = frame.shape[1]
    k = _odd(w / 85)  # 15 px @1280: wider than a line (≤ ~8 px), narrower than a player
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, GRASS_HSV_LO, GRASS_HSV_HI)
    closed = cv2.morphologyEx(green, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    grass_u8 = cv2.morphologyEx(closed, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    tophat = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    white = ((tophat >= min_contrast) & (grass_u8 > 0)).astype(np.uint8)
    # drop thick bright blobs (white / light shirts, shorts, socks, logos): whatever survives an opening wider than
    # any painted line (~8 px @1280 even in tight zooms), grown by the same kernel, is not a line.
    kt = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (_odd(w / 140),) * 2)  # 9 px @1280
    blobs = cv2.dilate(cv2.morphologyEx(white, cv2.MORPH_OPEN, kt), kt, iterations=2) > 0
    white[blobs] = 0
    grass = (grass_u8 > 0) & ~blobs  # occluded by the blob: neither a line nor verifiable grass
    _, lab, stats, _ = cv2.connectedComponentsWithStats(white, connectivity=8)
    min_len = min_len_frac * w
    long_ = np.maximum(stats[:, cv2.CC_STAT_WIDTH], stats[:, cv2.CC_STAT_HEIGHT]) >= min_len
    long_[0] = False
    return LineMask(long_[lab], grass)


def _project(H: np.ndarray, pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    hom = np.c_[pts, np.ones(len(pts))] @ H.T
    w = hom[:, 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        xy = hom[:, :2] / w[:, None]
    return xy, w


def render_lines(H_p2i: np.ndarray, size: tuple[int, int], template: PitchTemplate = FIFA_105x68, *,
                 thickness: int = 1, step_m: float = 0.5, image: np.ndarray | None = None,
                 colour: tuple[int, int, int] = (255, 0, 255), labels: bool = False) -> np.ndarray:
    """Draw the template through ``H_pitch→image``: uint8 mask (``image=None``; with ``labels=True`` each marking
    gets value ``i + 1`` in ``template.lines()`` order) or a copy of ``image`` with the lines in ``colour``.
    Samples behind the camera (w ≤ 0) or absurdly far break the polyline."""
    w_img, h_img = size
    out = np.zeros((h_img, w_img), np.uint8) if image is None else image.copy()
    H = np.asarray(H_p2i, float)
    sign = 1.0 if _project(H, template.keypoints.mean(0, keepdims=True))[1][0] > 0 else -1.0
    lim = 4.0 * max(w_img, h_img)
    for i, line in enumerate(template.lines(step_m)):
        if line.kind == "spot":
            continue
        col: int | tuple[int, int, int] = colour if image is not None else (i + 1 if labels else 255)
        xy, w = _project(H, line.points)
        ok = (sign * w > 1e-9) & np.all(np.abs(xy) < lim, axis=1)
        runs: list[np.ndarray] = []
        cur: list[np.ndarray] = []
        for p, good in zip(xy, ok, strict=True):
            if good:
                cur.append(p)
            elif cur:
                runs.append(np.array(cur))
                cur = []
        if cur:
            runs.append(np.array(cur))
        for r in runs:
            if len(r) >= 2:
                cv2.polylines(out, [np.round(r * 16).astype(np.int32)], False, col, thickness, cv2.LINE_8, shift=4)
    return out


@dataclass(frozen=True, slots=True)
class Alignment:
    precision: float
    recall: float
    quality: float          # h_quality 0..1
    n_template_px: int      # rendered template pixels on grass (observable)
    n_line_px: int          # detected white line pixels


def _soft(d: np.ndarray, tol: float) -> np.ndarray:
    return np.clip(1.0 - (d - tol) / (2.0 * tol), 0.0, 1.0)


def line_alignment(mask: LineMask, H_p2i: np.ndarray | None, template: PitchTemplate = FIFA_105x68, *,
                   tol_px: float | None = None, beta: float = 0.5, min_template_px: float | None = None,
                   min_marking_px: int = 25, border: int | None = None) -> Alignment:
    """Score ``H_pitch→image`` against a frame's ``LineMask`` (see module docstring).

    Precision is averaged **per marking** (touchline, halfway line, box sides, circle, …; each with at least
    ``min_marking_px`` observable pixels), so a shift along the touchline — which keeps the long touchlines on
    themselves — is still caught by the halfway line / box / circle."""
    w, h = mask.size
    if H_p2i is None or not np.all(np.isfinite(H_p2i)):
        return Alignment(0.0, 0.0, 0.0, 0, int(mask.white.sum()))
    tol = tol_px if tol_px is not None else max(2.0, 0.004 * w)  # 5 px @1280
    min_t = min_template_px if min_template_px is not None else 0.15 * w
    lab = render_lines(H_p2i, (w, h), template, labels=True)
    tmpl = lab > 0
    b = border if border is not None else round(0.008 * w)  # 10 px @1280
    inner = np.zeros((h, w), bool)
    inner[b:h - b, b:w - b] = True
    obs = tmpl & mask.grass & inner
    n_t, n_l = int(obs.sum()), int(mask.white.sum())
    if n_t < min_t:
        return Alignment(0.0, 0.0, 0.0, n_t, n_l)
    d_white = cv2.distanceTransform((~mask.white).astype(np.uint8), cv2.DIST_L2, 3)
    score = _soft(d_white[obs], tol)
    ids = lab[obs]
    counts = np.bincount(ids, minlength=256)
    sums = np.bincount(ids, weights=score, minlength=256)
    keep = counts >= min_marking_px
    p = float(np.mean(sums[keep] / counts[keep])) if keep.any() else float(score.mean())
    if n_l == 0:
        return Alignment(p, 0.0, 0.0, n_t, 0)
    d_tmpl = cv2.distanceTransform((~tmpl).astype(np.uint8), cv2.DIST_L2, 3)
    r = float(_soft(d_tmpl[mask.white], tol).mean())
    b2 = beta * beta
    q = (1 + b2) * p * r / (b2 * p + r) if p + r > 0 else 0.0
    return Alignment(p, r, float(q), n_t, n_l)


def h_quality(frame: np.ndarray, H_p2i: np.ndarray | None, template: PitchTemplate = FIFA_105x68) -> float:
    """Convenience: ``line_alignment(line_mask(frame), H_p2i).quality``."""
    return line_alignment(line_mask(frame), H_p2i, template).quality
