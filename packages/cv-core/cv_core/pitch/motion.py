"""Camera motion between consecutive frames of a broadcast shot, and homography propagation from sparse keyframes.

The pitch model runs every ``keypoint_every_n`` frames; in between the main camera pans and zooms. ``CameraMotion``
estimates, per frame, the image homography ``G_k`` mapping frame ``k`` pixels to frame ``k-1`` pixels from sparse
optical flow (Shi-Tomasi corners + pyramidal Lucas-Kanade with a forward-backward check + RANSAC; players move
against the background and are rejected as outliers). ``propagate`` chains them from each keyframe forwards and
backwards (``H_k = H_{k-1} · G_k``), never across a shot cut, and blends the two chains by distance through the
pitch positions of fixed image control points. Frames no chain reaches keep the caller's fallback (the temporal
smoother's interpolation). Licence-free: plain OpenCV.
"""
from __future__ import annotations

from collections.abc import Sequence

import cv2
import numpy as np

H33 = np.ndarray


class CameraMotion:
    """``m = CameraMotion(); G = m.step(frame)`` for every frame in order; ``reset()`` at a cut. ``G`` maps the
    current frame's pixels to the previous frame's (full resolution), ``None`` when it can't be trusted."""

    def __init__(self, *, scale: float = 0.5, max_corners: int = 500, min_inliers: int = 30,
                 fb_max_px: float = 1.0, ransac_px: float = 1.5) -> None:  # fmt: skip
        self.scale, self.max_corners, self.min_inliers = scale, max_corners, min_inliers
        self.fb_max_px, self.ransac_px = fb_max_px, ransac_px
        self._prev: np.ndarray | None = None
        s = np.diag([scale, scale, 1.0])
        self._S, self._Si = s, np.linalg.inv(s)

    def reset(self) -> None:
        self._prev = None

    def step(self, bgr: np.ndarray) -> H33 | None:
        small = cv2.resize(bgr, None, fx=self.scale, fy=self.scale, interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        prev, self._prev = self._prev, gray
        if prev is None:
            return None
        p0 = cv2.goodFeaturesToTrack(prev, self.max_corners, 0.01, 8, blockSize=7)
        if p0 is None or len(p0) < self.min_inliers:
            return None
        p1, st, _ = cv2.calcOpticalFlowPyrLK(prev, gray, p0, None, winSize=(21, 21), maxLevel=3)  # type: ignore[call-overload]
        back, st2, _ = cv2.calcOpticalFlowPyrLK(gray, prev, p1, None, winSize=(21, 21), maxLevel=3)  # type: ignore[call-overload]
        ok = (st.ravel() == 1) & (st2.ravel() == 1) & (np.linalg.norm((back - p0).reshape(-1, 2), axis=1)
                                                         < self.fb_max_px)  # fmt: skip
        if ok.sum() < self.min_inliers:
            return None
        G, inl = cv2.findHomography(p1[ok], p0[ok], cv2.RANSAC, self.ransac_px)  # current → previous
        if G is None or inl is None or int(inl.sum()) < self.min_inliers:
            return None
        full = self._Si @ G @ self._S
        return full / full[2, 2]


def _ctrl(H: H33, ctrl_img: np.ndarray) -> np.ndarray | None:
    hom = np.c_[ctrl_img, np.ones(len(ctrl_img))] @ H.T
    if not (np.all(hom[:, 2] > 0) or np.all(hom[:, 2] < 0)):
        return None
    p = hom[:, :2] / hom[:, 2:3]
    return p if np.all(np.abs(p) < 1e4) else None


def propagate(n: int, keys: dict[int, tuple[H33, float]], motion: Sequence[H33 | None], cuts: set[int], *,
              image_size: tuple[int, int], max_gap: int = 75, decay: float = 0.004
              ) -> tuple[list[H33 | None], np.ndarray]:  # fmt: skip
    """Per-frame image→pitch H for frames ``0..n-1`` (local indices) from keyframe estimates ``keys`` ({k: (H, q)})
    and ``motion[k]`` (frame k → frame k-1, ``None`` = broken). ``cuts`` = local indices starting a new shot.
    Quality = the nearest keyframe's ``q`` minus ``decay`` per frame of chaining; chains longer than ``max_gap``
    frames stop. → (H list with ``None`` where nothing reaches, qualities)."""
    w, h = image_size
    ctrl_img = np.array([[0.15 * w, 0.6 * h], [0.85 * w, 0.6 * h], [0.85 * w, 0.95 * h], [0.15 * w, 0.95 * h]],
                        np.float64)  # fmt: skip

    def chain(order: range, step_of) -> list[tuple[H33, int, float] | None]:  # type: ignore[no-untyped-def]
        out: list[tuple[H33, int, float] | None] = [None] * n
        state: tuple[H33, int, float] | None = None
        for k in order:
            state = step_of(k, state)
            if k in keys:
                state = (keys[k][0], 0, keys[k][1])
            if state is not None and state[1] > max_gap:
                state = None
            out[k] = state
        return out

    def fwd_step(k: int, s: tuple[H33, int, float] | None) -> tuple[H33, int, float] | None:
        if s is None or k in cuts or motion[k] is None:
            return None
        return (s[0] @ motion[k], s[1] + 1, s[2])  # img_k → img_{k-1} → pitch

    def bwd_step(k: int, s: tuple[H33, int, float] | None) -> tuple[H33, int, float] | None:
        nxt = k + 1
        g = motion[nxt] if nxt < n else None
        if s is None or g is None or nxt in cuts:
            return None
        return (s[0] @ np.linalg.inv(g), s[1] + 1, s[2])  # img_k → img_{k+1} → pitch

    fwd, bwd = chain(range(n), fwd_step), chain(range(n - 1, -1, -1), bwd_step)
    Hs: list[H33 | None] = [None] * n
    qs = np.zeros(n, np.float32)
    for k in range(n):
        cands = [c for c in (fwd[k], bwd[k]) if c is not None]
        if not cands:
            continue
        if len(cands) == 2:
            (Hf, df, qf), (Hb, db, qb) = cands
            cf, cb = _ctrl(Hf, ctrl_img), _ctrl(Hb, ctrl_img)
            if cf is not None and cb is not None:
                wf = db / (df + db) if df + db > 0 else 0.5
                Hs[k] = cv2.getPerspectiveTransform(ctrl_img.astype(np.float32),
                                                    (wf * cf + (1 - wf) * cb).astype(np.float32)).astype(np.float64)
                qs[k] = max(0.0, wf * (qf - decay * df) + (1 - wf) * (qb - decay * db))
                continue
            cands = [min(cands, key=lambda c: c[1])]
        H, d, q = cands[0]
        Hs[k], qs[k] = H / H[2, 2], max(0.0, q - decay * d)
    return Hs, qs
