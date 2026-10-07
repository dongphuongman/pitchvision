"""Image ↔ pitch homography from keypoints (S4, AD §6.3/§6.5), its quality, and temporal smoothing.

``estimate_homography(kp)`` fits **pitch → image** (residuals in pixels, the natural noise model for detected
keypoints) with OpenCV USAC-MAGSAC, refines on the inliers with confidence-weighted DLT + Gauss-Newton, and runs
degeneracy / plausibility checks for a broadcast main camera. ``HomographyEstimate.H`` is **image → pitch**
(metres, ``cv_core.pitch.template`` frame). ``h_quality`` comes from line alignment (``cv_core.pitch.lines``),
never from the fit residual (SP-03).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from cv_core.pitch.keypoints import PitchKeypoints
from cv_core.pitch.lines import Alignment, LineMask, line_alignment, line_mask, render_lines
from cv_core.pitch.template import FIFA_105x68, PitchTemplate

__all__ = [
    "Alignment",
    "HomographyConfig",
    "HomographyEstimate",
    "HomographySmoother",
    "LineMask",
    "estimate_homography",
    "fit_homography",
    "line_alignment",
    "line_mask",
    "project",
    "render_lines",
]


def project(H: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """Apply a 3×3 homography to ``[N, 2]`` points (NaN where w == 0)."""
    p = np.asarray(pts, float).reshape(-1, 2)
    hom = np.c_[p, np.ones(len(p))] @ np.asarray(H, float).T
    with np.errstate(divide="ignore", invalid="ignore"):
        return hom[:, :2] / hom[:, 2:3]


@dataclass(frozen=True)
class HomographyConfig:
    min_conf: float = 0.5            # keypoint confidence to be used at all
    border_frac: float = 0.01        # drop keypoints within this share of the frame edge / outside it
                                     # (SP-03: confident hallucinated points at the border)
    min_points: int = 4
    ransac_px_frac: float = 0.012    # MAGSAC threshold = frac × image width (15 px @1280)
    min_inlier_ratio: float = 0.5
    min_spread_m: float = 1.5        # 2nd principal std of the inlier pitch points (collinear = degenerate)
    min_dlt_cond: float = 1e-3       # σ8/σ7 of the normalised DLT matrix: ~0 when the fit has > 1 solution
    min_span_m: float = 5.0          # pitch metres covered by the image width at the inliers' row
    max_span_m: float = 220.0
    max_anisotropy: float = 25.0     # σmax/σmin of the image→pitch Jacobian at the inliers' centroid
    max_x_angle_deg: float = 60.0    # image direction of the pitch x-axis (touchlines) vs horizontal
    center_margin_m: float = 40.0    # image centre must project within the pitch grown by this margin
    weighted_refine: bool = True


@dataclass(frozen=True)
class HomographyEstimate:
    H: np.ndarray | None             # image → pitch (metres); None if rejected
    H_inv: np.ndarray | None         # pitch → image
    used: np.ndarray                 # bool [K]: passed confidence/border filters
    inliers: np.ndarray              # bool [K]: MAGSAC inliers (⊂ used)
    rmse_px: float                   # weighted inlier reprojection RMSE in px (diagnostic only, NOT a quality)
    reason: str = "ok"               # why it was rejected
    extra: dict[str, float] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.H is not None


def _normaliser(p: np.ndarray) -> np.ndarray:
    c = p.mean(0)
    s = math.sqrt(2) / max(float(np.sqrt(((p - c) ** 2).sum(1)).mean()), 1e-9)
    return np.array([[s, 0, -s * c[0]], [0, s, -s * c[1]], [0, 0, 1]])


def _dlt(src: np.ndarray, dst: np.ndarray, w: np.ndarray) -> tuple[np.ndarray, float]:
    """Weighted normalised DLT src→dst. Returns (H, σ8/σ7 conditioning)."""
    Ts, Td = _normaliser(src), _normaliser(dst)
    s = project(Ts, src)
    d = project(Td, dst)
    n = len(s)
    A = np.zeros((2 * n, 9))
    X, Y, u, v = s[:, 0], s[:, 1], d[:, 0], d[:, 1]
    A[0::2, 0:3] = np.c_[-X, -Y, -np.ones(n)]
    A[0::2, 6:9] = np.c_[u * X, u * Y, u]
    A[1::2, 3:6] = np.c_[-X, -Y, -np.ones(n)]
    A[1::2, 6:9] = np.c_[v * X, v * Y, v]
    sw = np.sqrt(np.repeat(w, 2))
    _, sv, vt = np.linalg.svd(A * sw[:, None])
    Hn = vt[-1].reshape(3, 3)
    H = np.linalg.inv(Td) @ Hn @ Ts
    cond = float(sv[-2] / sv[-3]) if len(sv) >= 9 and sv[-3] > 0 else 0.0
    return H / H[2, 2] if abs(H[2, 2]) > 1e-12 else H, cond


def _refine(H: np.ndarray, src: np.ndarray, dst: np.ndarray, w: np.ndarray, iters: int = 10) -> np.ndarray:
    """Weighted Gauss-Newton (Levenberg damping) on the src→dst reprojection error, h33 = 1."""
    h = (H / H[2, 2]).reshape(9)[:8].copy()
    X, Y = src[:, 0], src[:, 1]

    def resid(hv: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        den = hv[6] * X + hv[7] * Y + 1
        u = (hv[0] * X + hv[1] * Y + hv[2]) / den
        v = (hv[3] * X + hv[4] * Y + hv[5]) / den
        return u, v, den, np.r_[(u - dst[:, 0]) * np.sqrt(w), (v - dst[:, 1]) * np.sqrt(w)]

    lam = 1e-3
    _, _, _, r = resid(h)
    cost = float(r @ r)
    for _ in range(iters):
        u, v, den, r = resid(h)
        n = len(X)
        J = np.zeros((2 * n, 8))
        J[:n, 0], J[:n, 1], J[:n, 2] = X / den, Y / den, 1 / den
        J[:n, 6], J[:n, 7] = -u * X / den, -u * Y / den
        J[n:, 3], J[n:, 4], J[n:, 5] = X / den, Y / den, 1 / den
        J[n:, 6], J[n:, 7] = -v * X / den, -v * Y / den
        J *= np.sqrt(np.r_[w, w])[:, None]
        A = J.T @ J
        g = J.T @ r
        try:
            step = np.linalg.solve(A + lam * np.diag(np.diag(A) + 1e-12), -g)
        except np.linalg.LinAlgError:
            break
        _, _, _, r2 = resid(h + step)
        c2 = float(r2 @ r2)
        if np.isfinite(c2) and c2 < cost:
            h, cost, lam = h + step, c2, lam * 0.3
            if np.abs(step).max() < 1e-10:
                break
        else:
            lam *= 10
    return np.r_[h, 1.0].reshape(3, 3)


def fit_homography(src: np.ndarray, dst: np.ndarray, weights: np.ndarray | None = None, *,
                   refine: bool = True) -> tuple[np.ndarray, float]:
    """Weighted DLT (+ Gauss-Newton) ``src → dst`` on ≥ 4 points. Returns (H, DLT conditioning)."""
    src, dst = np.asarray(src, float), np.asarray(dst, float)
    w = np.ones(len(src)) if weights is None else np.asarray(weights, float)
    H, cond = _dlt(src, dst, w)
    if refine and len(src) > 4:
        H = _refine(H, src, dst, w)
    return H, cond


def _jacobian(H: np.ndarray, p: np.ndarray, eps: float = 1.0) -> np.ndarray:
    pts = np.array([p, p + np.array([eps, 0.0]), p + np.array([0.0, eps])])
    q = project(H, pts)
    return np.c_[(q[1] - q[0]) / eps, (q[2] - q[0]) / eps]  # columns = d/dx, d/dy


def _plausibility(H_i2p: np.ndarray, H_p2i: np.ndarray, img_in: np.ndarray, size: tuple[int, int],
                  template: PitchTemplate, cfg: HomographyConfig) -> tuple[str, dict[str, float]]:
    w_img, h_img = size
    # 1) every inlier in front of the camera, and the image centre / bottom below the horizon
    def w_of(H: np.ndarray, p: np.ndarray) -> np.ndarray:
        return np.c_[p, np.ones(len(p))] @ H[2]
    sign = np.sign(w_of(H_i2p, img_in))
    if not (np.all(sign > 0) or np.all(sign < 0)):
        return "inliers straddle the horizon", {}
    probe = np.array([[w_img / 2, h_img / 2], [w_img / 2, h_img * 0.95]])
    if np.any(np.sign(w_of(H_i2p, probe)) != sign[0]):
        return "image centre above the horizon", {}
    c = img_in.mean(0)
    # 2) no mirror: pitch (x right, y towards the near touchline) → image (x right, y down) preserves orientation
    J = _jacobian(H_p2i, project(H_i2p, c[None])[0], 0.1)
    if np.linalg.det(J) <= 0:
        return "mirrored (left/right swapped)", {}
    # 3) scale: pitch metres spanned by the image width at the inliers' row
    row = project(H_i2p, np.array([[0.0, c[1]], [float(w_img), c[1]]]))
    span = float(np.linalg.norm(row[1] - row[0]))
    sv = np.linalg.svd(_jacobian(H_i2p, c), compute_uv=False)
    aniso = float(sv[0] / max(sv[1], 1e-12))
    xdir = J[:, 0]
    angle = abs(math.degrees(math.atan2(xdir[1], xdir[0])))  # > 90° when the x-axis points image-left
    centre = project(H_i2p, np.array([[w_img / 2, h_img / 2]]))
    stats = {"span_m": span, "anisotropy": aniso, "x_angle_deg": angle,
             "centre_x": float(centre[0, 0]), "centre_y": float(centre[0, 1])}
    if not np.isfinite(span) or not cfg.min_span_m <= span <= cfg.max_span_m:
        return f"implausible scale (image width = {span:.1f} m)", stats
    if aniso > cfg.max_anisotropy:
        return f"implausible perspective (anisotropy {aniso:.1f})", stats
    if xdir[0] <= 0 or angle > cfg.max_x_angle_deg:
        return f"touchline direction implausible ({angle:.0f}°)", stats
    if not template.contains(centre, cfg.center_margin_m)[0]:
        return "image centre projects off the pitch", stats
    return "ok", stats


def estimate_homography(kp: PitchKeypoints, template: PitchTemplate = FIFA_105x68,
                        cfg: HomographyConfig | None = None) -> HomographyEstimate:
    """Keypoints of one frame → ``HomographyEstimate`` (``.H`` image→pitch, or ``None`` + ``reason``)."""
    cfg = cfg or HomographyConfig()
    K = kp.num_keypoints
    w_img, h_img = kp.image_size
    m = cfg.border_frac * w_img
    xy, conf = np.asarray(kp.xy, float), np.asarray(kp.conf, float)
    world_ok = np.all(np.isfinite(kp.world), axis=1) if kp.world is not None else np.ones(K, bool)
    used = ((conf >= cfg.min_conf) & np.all(np.isfinite(xy), axis=1) & world_ok
            & (xy[:, 0] > m) & (xy[:, 0] < w_img - m) & (xy[:, 1] > m) & (xy[:, 1] < h_img - m))
    none = np.zeros(K, bool)

    def fail(reason: str, inl: np.ndarray = none, rmse: float = float("nan"),
             extra: dict[str, float] | None = None) -> HomographyEstimate:
        return HomographyEstimate(None, None, used, inl, rmse, reason, extra or {})

    idx = np.flatnonzero(used)
    if len(idx) < cfg.min_points:
        return fail(f"{len(idx)} usable keypoints < {cfg.min_points}")
    tpl = np.asarray(kp.world, float) if kp.world is not None else template.keypoints[: K]
    src, dst, wts = tpl[idx], xy[idx], conf[idx]
    thr = cfg.ransac_px_frac * w_img
    if len(idx) == 4:
        H0, mask = cv2.getPerspectiveTransform(src.astype(np.float32), dst.astype(np.float32)), np.ones(4, bool)
    else:
        H0, mk = cv2.findHomography(src, dst, cv2.USAC_MAGSAC, thr, maxIters=2000, confidence=0.999)
        if H0 is None:
            return fail("MAGSAC found no model")
        mask = mk.ravel().astype(bool)
    inliers = none.copy()
    inliers[idx[mask]] = True
    n_in = int(mask.sum())
    if n_in < cfg.min_points or n_in / len(idx) < cfg.min_inlier_ratio:
        return fail(f"{n_in}/{len(idx)} inliers", inliers)
    s_in, d_in, w_in = src[mask], dst[mask], wts[mask]
    sv = np.linalg.svd(s_in - s_in.mean(0), compute_uv=False)
    spread = float(sv[1] / math.sqrt(len(s_in)))
    if spread < cfg.min_spread_m:
        return fail(f"keypoints (nearly) collinear on the pitch (spread {spread:.2f} m)", inliers)
    H_p2i, cond = fit_homography(s_in, d_in, w_in if cfg.weighted_refine else None, refine=True)
    if n_in > 4 and cond < cfg.min_dlt_cond:
        return fail(f"degenerate configuration (DLT cond {cond:.1e})", inliers)
    if not np.all(np.isfinite(H_p2i)) or abs(np.linalg.det(H_p2i)) < 1e-12:
        return fail("singular homography", inliers)
    r = np.linalg.norm(project(H_p2i, s_in) - d_in, axis=1)
    rmse = float(np.sqrt(np.sum(w_in * r**2) / np.sum(w_in)))
    H_i2p = np.linalg.inv(H_p2i)
    H_i2p /= H_i2p[2, 2] if abs(H_i2p[2, 2]) > 1e-12 else 1.0
    reason, stats = _plausibility(H_i2p, H_p2i, d_in, (w_img, h_img), template, cfg)
    stats["spread_m"], stats["dlt_cond"], stats["n_inliers"] = spread, cond, float(n_in)
    if reason != "ok":
        return fail(reason, inliers, rmse, stats)
    return HomographyEstimate(H_i2p, H_p2i / H_p2i[2, 2], used, inliers, rmse, "ok", stats)


# --- temporal smoothing ---------------------------------------------------------------------------


@dataclass
class _State:
    idx: int
    ctrl: np.ndarray  # [4, 2] pitch metres of the fixed image control points
    q: float


class HomographySmoother:
    """Per-shot temporal filter for sparse keypoint homographies (AD §6.3: ``update`` every ``keypoint_every``
    frames, ``at`` every frame; ``reset`` at every shot cut — never smooth across a cut).

    Parametrisation: the pitch positions (metres) of 4 fixed image control points in the lower part of the frame
    (always on the grass in a main-camera shot). Unlike the 9 raw H entries, these are well-conditioned,
    physically meaningful (jumps are in metres) and any 4 of them define H again.

    - exponential smoothing, time constant ``window/2`` frames, step weighted by the measurement's quality;
    - a measurement whose control points move more than ``max_jump_m + jump_m_per_frame·gap`` (median, metres)
      from the state is an outlier — unless ``confirm`` consecutive outliers agree with each other (a real fast
      pan / zoom): the state then re-initialises on them;
    - measurements below ``min_quality`` are ignored;
    - ``at(idx)`` interpolates linearly between states of the same shot, holds the last one for up to
      ``max_hold`` frames, and back-fills the shot's first ``max_hold`` frames from its first state.
    """

    def __init__(self, window: int = 15, *, image_size: tuple[int, int] = (1280, 720), min_quality: float = 0.3,
                 max_jump_m: float = 3.0, jump_m_per_frame: float = 0.6, confirm: int = 2, max_hold: int = 50,
                 history: int = 512) -> None:
        self.window = window
        self.image_size = image_size
        self.min_quality = min_quality
        self.max_jump_m = max_jump_m
        self.jump_m_per_frame = jump_m_per_frame
        self.confirm = confirm
        self.max_hold = max_hold
        self.history = history
        w, h = image_size
        self.ctrl_img = np.array([[0.15 * w, 0.6 * h], [0.85 * w, 0.6 * h],
                                  [0.85 * w, 0.95 * h], [0.15 * w, 0.95 * h]], np.float32)
        self._shots: list[tuple[int, list[_State]]] = [(-(2**62), [])]
        self._pending: list[_State] = []

    # -- API
    def reset(self, frame_idx: int) -> None:
        """Shot cut at ``frame_idx``: later states never mix with earlier ones."""
        self._shots.append((frame_idx, []))
        self._pending = []
        total = sum(len(s) for _, s in self._shots)
        while total > self.history and len(self._shots) > 1:
            total -= len(self._shots.pop(0)[1])

    def update(self, frame_idx: int, H: np.ndarray | None, quality: float) -> bool:
        """Feed one measurement (``H`` image→pitch). Returns True if it changed the state."""
        if H is None or not np.all(np.isfinite(H)) or quality < self.min_quality:
            return False
        ctrl = self._ctrl(H)
        if ctrl is None:
            return False
        meas = _State(frame_idx, ctrl, float(quality))
        states = self._shots[-1][1]
        if not states:
            states.append(meas)
            return True
        last = states[-1]
        gap = max(1, frame_idx - last.idx)
        allowed = self.max_jump_m + self.jump_m_per_frame * gap
        if self._dev(meas.ctrl, last.ctrl) > allowed:
            if self._pending and self._dev(meas.ctrl, self._pending[-1].ctrl) > (
                    self.max_jump_m + self.jump_m_per_frame * max(1, frame_idx - self._pending[-1].idx)):
                self._pending = []
            self._pending.append(meas)
            if len(self._pending) >= self.confirm:
                states.append(meas)  # the camera really moved: re-initialise
                self._pending = []
                return True
            return False
        self._pending = []
        tau = max(self.window / 2.0, 1e-6)
        a = 1.0 - math.exp(-gap * meas.q / tau)
        states.append(_State(frame_idx, last.ctrl + a * (meas.ctrl - last.ctrl), last.q + a * (meas.q - last.q)))
        if len(states) > self.history:
            del states[0]
        return True

    def at(self, frame_idx: int) -> tuple[np.ndarray | None, float]:
        """(H image→pitch, quality) for a frame; (None, 0.0) when no reliable estimate in this shot."""
        _, states = self._shot_of(frame_idx)
        if not states:
            return None, 0.0
        if frame_idx <= states[0].idx:
            s = states[0]
            return (self._H(s.ctrl), s.q) if s.idx - frame_idx <= self.max_hold else (None, 0.0)
        if frame_idx >= states[-1].idx:
            s = states[-1]
            return (self._H(s.ctrl), s.q) if frame_idx - s.idx <= self.max_hold else (None, 0.0)
        idxs = [s.idx for s in states]
        j = int(np.searchsorted(idxs, frame_idx, side="right"))
        a, b = states[j - 1], states[j]
        if b.idx - a.idx > 2 * self.max_hold:
            return None, 0.0
        t = (frame_idx - a.idx) / (b.idx - a.idx)
        return self._H(a.ctrl + t * (b.ctrl - a.ctrl)), a.q + t * (b.q - a.q)

    # -- internals
    def _shot_of(self, frame_idx: int) -> tuple[int, list[_State]]:
        for start, states in reversed(self._shots):
            if frame_idx >= start:
                return start, states
        return self._shots[0]

    def _ctrl(self, H: np.ndarray) -> np.ndarray | None:
        hom = np.c_[self.ctrl_img, np.ones(4)] @ np.asarray(H, float).T
        if not (np.all(hom[:, 2] > 0) or np.all(hom[:, 2] < 0)):
            return None  # a control point is above the horizon: not a main-camera view
        p = hom[:, :2] / hom[:, 2:3]
        return p if np.all(np.abs(p) < 1e4) else None

    def _H(self, ctrl: np.ndarray) -> np.ndarray:
        return cv2.getPerspectiveTransform(self.ctrl_img, ctrl.astype(np.float32)).astype(np.float64)

    @staticmethod
    def _dev(a: np.ndarray, b: np.ndarray) -> float:
        return float(np.median(np.linalg.norm(a - b, axis=1)))
