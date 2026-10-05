"""Synthetic broadcast main-camera views of the FIFA template: known H + rendered frames for pitch tests."""
from __future__ import annotations

import cv2
import numpy as np
from cv_core.pitch import FIFA_105x68, PitchTemplate, project, render_lines


def camera_homography(*, target: tuple[float, float] = (52.5, 34.0), cam_x: float = 52.5, back_m: float = 40.0,
                      height_m: float = 22.0, focal_px: float = 1100.0, size: tuple[int, int] = (1280, 720),
                      roll_deg: float = 0.0, template: PitchTemplate = FIFA_105x68) -> np.ndarray:
    """Pinhole camera behind the near touchline (y = width + back_m) at ``height_m``, aimed at ``target``.

    World frame: x along the pitch, y towards the near touchline, z DOWN (right-handed). Returns H pitch→image."""
    w, h = size
    C = np.array([cam_x, template.width + back_m, -height_m])
    T = np.array([target[0], target[1], 0.0])
    f = (T - C) / np.linalg.norm(T - C)
    r = np.cross([0.0, 0.0, 1.0], f)
    r /= np.linalg.norm(r)
    d = np.cross(f, r)
    R = np.stack([r, d, f])
    if roll_deg:
        a = np.radians(roll_deg)
        R = np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]]) @ R
    K = np.array([[focal_px, 0, w / 2], [0, focal_px, h / 2], [0, 0, 1]])
    P = K @ np.c_[R, -R @ C]
    H = P[:, [0, 1, 3]]
    return H / H[2, 2]


def render_frame(H_p2i: np.ndarray, size: tuple[int, int] = (1280, 720), *, seed: int = 0, line_px: int = 3,
                 noise: float = 6.0, players: int = 18, clutter: int = 0,
                 template: PitchTemplate = FIFA_105x68) -> np.ndarray:
    """Grass (mowing stripes) inside the pitch + 5 m, grey stands outside, white markings drawn through H,
    blur + noise, player-sized coloured occluders and optional white non-line clutter blobs."""
    rng = np.random.default_rng(seed)
    w, h = size
    img = np.full((h, w, 3), (70, 70, 80), np.uint8)
    # grass polygon (sampled densely so the perspective edge is right)
    L, W, g = template.length, template.width, 5.0
    edge = np.concatenate([np.linspace((-g, -g), (L + g, -g), 200), np.linspace((L + g, -g), (L + g, W + g), 200),
                           np.linspace((L + g, W + g), (-g, W + g), 200), np.linspace((-g, W + g), (-g, -g), 200)])
    poly = project(H_p2i, edge)
    poly = np.clip(poly, -4 * w, 5 * w)
    cv2.fillPoly(img, [np.round(poly).astype(np.int32)], (40, 130, 45))
    # mowing stripes every 5.25 m across x
    for i in range(0, 20, 2):
        x0, x1 = i * L / 20, (i + 1) * L / 20
        quad = project(H_p2i, np.array([[x0, -g], [x1, -g], [x1, W + g], [x0, W + g]]))
        cv2.fillPoly(img, [np.round(np.clip(quad, -4 * w, 5 * w)).astype(np.int32)], (48, 145, 55))
    img = render_lines(H_p2i, size, template, thickness=line_px, image=img, colour=(235, 240, 240))
    img = cv2.GaussianBlur(img, (3, 3), 0)
    for _ in range(players):
        x, y = int(rng.integers(0, w - 20)), int(rng.integers(h // 4, h - 40))
        col = [(30, 30, 200), (200, 200, 30), (20, 20, 20)][int(rng.integers(0, 3))]
        cv2.rectangle(img, (x, y), (x + 14, y + 38), col, -1)
    for _ in range(clutter):
        x, y = int(rng.integers(0, w - 40)), int(rng.integers(h // 3, h - 20))
        cv2.ellipse(img, (x, y), (int(rng.integers(5, 20)), int(rng.integers(3, 8))), 0, 0, 360, (240, 240, 240), -1)
    img = img.astype(np.float32) + rng.normal(0, noise, img.shape).astype(np.float32)
    return np.clip(img, 0, 255).astype(np.uint8)


def keypoints_from_H(H_p2i: np.ndarray, size: tuple[int, int] = (1280, 720), *, noise_px: float = 1.0,
                     seed: int = 0, template: PitchTemplate = FIFA_105x68) -> tuple[np.ndarray, np.ndarray]:
    """Template keypoints projected through H (+ Gaussian noise); conf 0.9 inside the frame, 0.02 outside."""
    rng = np.random.default_rng(seed)
    xy = project(H_p2i, template.keypoints) + rng.normal(0, noise_px, (len(template.keypoints), 2))
    w, h = size
    inside = (xy[:, 0] > 2) & (xy[:, 0] < w - 2) & (xy[:, 1] > 2) & (xy[:, 1] < h - 2)
    conf = np.where(inside, 0.9, 0.02)
    return xy.astype(np.float32), conf.astype(np.float32)


def perturb(H_p2i: np.ndarray, *, shift_m: tuple[float, float] = (0.0, 0.0), scale: float = 1.0,
            rot_deg: float = 0.0, centre: tuple[float, float] = (52.5, 34.0)) -> np.ndarray:
    """H composed with a similarity of the pitch plane around ``centre`` (a 'wrong' calibration)."""
    a = np.radians(rot_deg)
    cx, cy = centre
    S = np.array([[scale * np.cos(a), -scale * np.sin(a), 0], [scale * np.sin(a), scale * np.cos(a), 0], [0, 0, 1]])
    T0 = np.array([[1, 0, -cx], [0, 1, -cy], [0, 0, 1]])
    T1 = np.array([[1, 0, cx + shift_m[0]], [0, 1, cy + shift_m[1]], [0, 0, 1]])
    return H_p2i @ np.linalg.inv(T1 @ S @ T0)
