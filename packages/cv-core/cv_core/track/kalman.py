"""Constant-velocity Kalman filter in (cx, cy, aspect, h) space — the SORT / DeepSORT / ByteTrack box model.

Re-implemented from the published ByteTrack design (Zhang et al. 2022; reference code MIT); no code copied from
Ultralytics. State ``[cx, cy, a, h, vcx, vcy, va, vh]``; noise scales with box height.
"""
from __future__ import annotations

import numpy as np

STD_POS = 1.0 / 20
STD_VEL = 1.0 / 160

_F = np.eye(8)
_F[:4, 4:] = np.eye(4)
_H = np.eye(4, 8)


def xyxy_to_xyah(b: np.ndarray) -> np.ndarray:
    b = np.asarray(b, np.float64)
    w, h = b[..., 2] - b[..., 0], b[..., 3] - b[..., 1]
    return np.stack([b[..., 0] + w / 2, b[..., 1] + h / 2, w / np.maximum(h, 1e-6), h], axis=-1)


def xyah_to_xyxy(s: np.ndarray) -> np.ndarray:
    s = np.asarray(s, np.float64)
    h = s[..., 3]
    w = s[..., 2] * h
    return np.stack([s[..., 0] - w / 2, s[..., 1] - h / 2, s[..., 0] + w / 2, s[..., 1] + h / 2], axis=-1)


def initiate(meas: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mean = np.r_[meas, np.zeros(4)]
    h = meas[3]
    std = np.array([2 * STD_POS * h, 2 * STD_POS * h, 1e-2, 2 * STD_POS * h,
                    10 * STD_VEL * h, 10 * STD_VEL * h, 1e-5, 10 * STD_VEL * h])
    return mean, np.diag(std**2)


def predict(means: np.ndarray, covs: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Batched predict: means [N, 8], covs [N, 8, 8]."""
    if len(means) == 0:
        return means, covs
    h = means[:, 3]
    std = np.stack([STD_POS * h, STD_POS * h, np.full_like(h, 1e-2), STD_POS * h,
                    STD_VEL * h, STD_VEL * h, np.full_like(h, 1e-5), STD_VEL * h], axis=1)
    q = np.einsum("ni,ij->nij", std**2, np.eye(8))
    means = means @ _F.T
    covs = np.einsum("ij,njk,lk->nil", _F, covs, _F) + q
    return means, covs


def update(mean: np.ndarray, cov: np.ndarray, meas: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    h = mean[3]
    r = np.diag(np.array([STD_POS * h, STD_POS * h, 1e-1, STD_POS * h]) ** 2)
    proj_mean = _H @ mean
    proj_cov = _H @ cov @ _H.T + r
    gain = np.linalg.solve(proj_cov, (cov @ _H.T).T).T
    new_mean = mean + gain @ (meas - proj_mean)
    new_cov = cov - gain @ proj_cov @ gain.T
    return new_mean, new_cov
