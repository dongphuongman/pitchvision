"""Unsupervised two-team model (S3 calibrate → S4 per-track vote; AD §6.2, SP-04).

``fit_team_model(embeddings)`` on L2-normalised SigLIP shirt embeddings (numpy only):

1. **Split** — candidate 2-way partitions from spectral clustering on a self-tuning kNN cosine graph
   (k ∈ {10, 15}, 1–3 eigenvectors) plus plain KMeans on PCA-8. Each candidate is scored by its *cross-validated*
   Fisher ratio: an LDA direction is fitted on one half of the crops and the class separation measured on the other
   half (and vice versa). The best balanced candidate wins. The graph captures the kit manifold like SP-04's UMAP
   does; plain PCA→KMeans does not — on SigLIP crops the top PCA directions are blur / scale / pose, and PCA→KMeans
   agreed with UMAP on only ~60 % of the crops for 8 / 19 golden matches (the montage showed PCA wrong).
2. **Predict** — PCA-32 followed by whitening with the (ridge-regularised) within-team covariance, so that
   nearest-centroid = the LDA rule. Projection and centroids are plain matrices in ``team_model.json``.
3. **Outliers** — per-team radius = median + ``outlier_k``·MAD of member distances (refit once without outliers);
   farther crops → team -1 (referees, goalkeepers, occluded or mixed crops).
4. **Failure** (``TeamSeparationFailed`` → ``TEAM_SEPARATION_FAILED``) — too few crops, no balanced candidate, or a
   cross-validated Fisher ratio below ``min_fisher`` (golden matches: 5.0–10.5; a single Gaussian blob split in
   two: ≈ 2.5).

Why not UMAP itself: umap-learn + numba + pynndescent + scikit-learn would add ~150 MB of runtime deps and JIT
warm-up to every GPU image, and transforming new points is neither portable nor exactly reproducible from JSON.
``eval/teams_pca_vs_umap.py`` compares this model with SP-04's UMAP→KMeans on 19 golden matches.
"""
from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

TEAM_MODEL_FORMAT = "pv-team-model/v1"
NO_TEAM = -1


class TeamSeparationFailed(Exception):
    """Calibration could not split the players into two teams; the job fails with ``TEAM_SEPARATION_FAILED``."""

    code = "TEAM_SEPARATION_FAILED"

    def __init__(self, reason: str, detail: str = "", stats: Mapping[str, Any] | None = None) -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason, self.detail = reason, detail
        self.stats = dict(stats or {})


@dataclass(frozen=True)
class FitParams:
    n_components: int = 32               # PCA dims before whitening
    knn: tuple[int, ...] = (10, 15)      # spectral graph neighbours
    spectral_dims: tuple[int, ...] = (1, 2, 3)
    ridge: float = 0.1                   # within-class covariance shrinkage (× mean eigenvalue)
    n_init: int = 10
    max_iter: int = 100
    seed: int = 0
    outlier_k: float = 3.0               # radius = median + k · 1.4826 · MAD of a team's member distances
    min_crops: int = 40                  # fewer crops → TEAM_SEPARATION_FAILED(too_few_crops)
    min_share: float = 0.2               # a candidate's smaller team must hold ≥ this share
    min_fisher: float = 4.0              # cross-validated Fisher ratio below → (not_separable)
    silhouette_sample: int = 2000


@dataclass
class TeamModel:
    embedder: str
    mean: np.ndarray           # [D]
    components: np.ndarray     # [k, D] (PCA rows, whitened: Euclidean distance here = within-team Mahalanobis)
    centroids: np.ndarray      # [2, k]
    radius: np.ndarray         # [2] outlier distance per team
    embedder_sha256: str | None = None
    stats: dict[str, Any] = field(default_factory=dict)
    samples: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    # ---------------------------------------------------------------- inference
    def project(self, emb: np.ndarray) -> np.ndarray:
        x = np.asarray(emb, np.float64)
        x = x / (np.linalg.norm(x, axis=1, keepdims=True) + 1e-12)  # fitted on L2-normalised embeddings
        return np.asarray((x - self.mean) @ self.components.T)

    def distances(self, emb: np.ndarray) -> np.ndarray:
        """[N, 2] Euclidean distances to both centroids in the projected space."""
        z = self.project(emb)
        return np.asarray(np.linalg.norm(z[:, None, :] - self.centroids[None], axis=2))

    def predict(self, emb: np.ndarray) -> np.ndarray:
        """int8 [N]: 0 / 1, or -1 when the crop is an outlier for its nearest team."""
        if len(emb) == 0:
            return np.zeros(0, np.int8)
        d = self.distances(emb)
        lab = d.argmin(1)
        out = lab.astype(np.int8)
        out[d[np.arange(len(lab)), lab] > self.radius[lab]] = NO_TEAM
        return out

    # ---------------------------------------------------------------- JSON
    def to_dict(self) -> dict[str, Any]:
        return {
            "format": TEAM_MODEL_FORMAT,
            "embedder": {"name": self.embedder, "sha256": self.embedder_sha256},
            "projection": {"type": "linear", "mean": self.mean.tolist(), "components": self.components.tolist()},
            "centroids": self.centroids.tolist(),
            "thresholds": {"outlier_radius": self.radius.tolist()},
            "stats": self.stats,
            "samples": self.samples,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> TeamModel:
        if d.get("format") != TEAM_MODEL_FORMAT:
            raise ValueError(f"not a {TEAM_MODEL_FORMAT} file (format={d.get('format')!r})")
        proj = d["projection"]
        if proj.get("type") != "linear":
            raise ValueError(f"unsupported projection {proj.get('type')!r}")
        return cls(embedder=d["embedder"]["name"], embedder_sha256=d["embedder"].get("sha256"),
                   mean=np.asarray(proj["mean"], np.float64), components=np.asarray(proj["components"], np.float64),
                   centroids=np.asarray(d["centroids"], np.float64),
                   radius=np.asarray(d["thresholds"]["outlier_radius"], np.float64),
                   stats=dict(d.get("stats") or {}), samples={k: list(v) for k, v in (d.get("samples") or {}).items()})

    def save(self, path: str | os.PathLike[str]) -> Path:
        p = Path(path)
        p.write_text(json.dumps(self.to_dict(), separators=(",", ":")))
        return p

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> TeamModel:
        return cls.from_dict(json.loads(Path(path).read_text()))


# -------------------------------------------------------------------- numpy building blocks
def pca(x: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(mean [D], components [k, D], explained variance ratio [k]); sign-fixed so the result is deterministic."""
    mean = x.mean(0)
    xc = x - mean
    _, s, vt = np.linalg.svd(xc, full_matrices=False)
    k = min(k, vt.shape[0])
    comp = vt[:k]
    signs = np.sign(comp[np.arange(k), np.abs(comp).argmax(1)])
    comp = comp * signs[:, None]
    var = s**2
    return mean, comp, var[:k] / max(var.sum(), 1e-12)


def kmeans(x: np.ndarray, k: int = 2, *, n_init: int = 10, max_iter: int = 100, seed: int = 0,
           tol: float = 1e-8) -> tuple[np.ndarray, np.ndarray, float]:
    """Lloyd's k-means with k-means++ seeding, best of ``n_init`` by inertia → (centroids, labels, inertia)."""
    rng = np.random.default_rng(seed)
    n = len(x)
    if n < k:
        raise ValueError(f"need at least {k} points")
    best: tuple[np.ndarray, np.ndarray, float] | None = None
    for _ in range(n_init):
        c = np.empty((k, x.shape[1]))
        c[0] = x[rng.integers(n)]
        d2 = ((x - c[0]) ** 2).sum(1)
        for j in range(1, k):
            p = d2 / d2.sum() if d2.sum() > 0 else np.full(n, 1 / n)
            c[j] = x[rng.choice(n, p=p)]
            d2 = np.minimum(d2, ((x - c[j]) ** 2).sum(1))
        lab = np.zeros(n, np.int64)
        for _ in range(max_iter):
            dist = ((x[:, None, :] - c[None]) ** 2).sum(2)
            lab = dist.argmin(1)
            new = np.array([x[lab == j].mean(0) if (lab == j).any() else c[j] for j in range(k)])
            shift = float(((new - c) ** 2).sum())
            c = new
            if shift <= tol:
                break
        dist = ((x[:, None, :] - c[None]) ** 2).sum(2)
        lab = dist.argmin(1)
        inertia = float(dist[np.arange(n), lab].sum())
        if best is None or inertia < best[2]:
            best = (c, lab, inertia)
    assert best is not None
    return best


def silhouette(x: np.ndarray, labels: np.ndarray, *, sample: int = 2000, seed: int = 0) -> float:
    """Mean silhouette coefficient (Euclidean) over at most ``sample`` points."""
    if len(np.unique(labels)) < 2:
        return 0.0
    if len(x) > sample:
        idx = np.random.default_rng(seed).choice(len(x), sample, replace=False)
        x, labels = x[idx], labels[idx]
    d = np.sqrt(np.maximum(((x[:, None, :] - x[None]) ** 2).sum(2), 0))
    s = np.zeros(len(x))
    for i in range(len(x)):
        same = labels == labels[i]
        n_same = same.sum() - 1
        if n_same == 0:
            continue
        a = d[i, same].sum() / n_same
        b = min(d[i, labels == other].mean() for other in np.unique(labels) if other != labels[i])
        s[i] = (b - a) / max(a, b) if max(a, b) > 0 else 0.0
    return float(s.mean())


def spectral_embedding(x: np.ndarray, k: int, dims: int) -> np.ndarray:
    """Rows of the top non-trivial eigenvectors of the normalised affinity of a self-tuning kNN cosine graph
    (Zelnik-Manor & Perona 2004), row-normalised (Ng–Jordan–Weiss) → [N, dims]."""
    n = len(x)
    k = max(1, min(k, n - 1))
    s = x @ x.T
    np.fill_diagonal(s, -np.inf)
    nn = np.argpartition(-s, k - 1, axis=1)[:, :k]
    dist = 1.0 - np.take_along_axis(s, nn, 1)
    sigma = dist.max(1) + 1e-6
    rows = np.repeat(np.arange(n), k)
    w = np.zeros((n, n))
    w[rows, nn.ravel()] = np.exp(-(dist.ravel() ** 2) / (sigma[rows] * sigma[nn.ravel()]))
    w = np.maximum(w, w.T)
    dinv = 1.0 / np.sqrt(w.sum(1) + 1e-12)
    _, vecs = np.linalg.eigh(dinv[:, None] * w * dinv[None])
    v = vecs[:, -1 - dims:-1]
    return np.asarray(v / (np.linalg.norm(v, axis=1, keepdims=True) + 1e-12))


def _within_cov(z: np.ndarray, lab: np.ndarray, ridge: float) -> np.ndarray:
    sw = np.zeros((z.shape[1], z.shape[1]))
    for j in (0, 1):
        zj = z[lab == j] - z[lab == j].mean(0)
        sw += zj.T @ zj
    sw /= max(len(z), 1)
    return np.asarray(sw + ridge * np.trace(sw) / len(sw) * np.eye(len(sw)))


def _fisher(p: np.ndarray, lab: np.ndarray) -> float:
    p0, p1 = p[lab == 0], p[lab == 1]
    if len(p0) < 2 or len(p1) < 2:
        return 0.0
    pooled = math.sqrt((p0.var() * len(p0) + p1.var() * len(p1)) / len(p))
    return float(abs(p1.mean() - p0.mean()) / max(pooled, 1e-12))


def cv_fisher(z: np.ndarray, lab: np.ndarray, *, ridge: float = 0.1, seed: int = 0) -> float:
    """Two-fold cross-validated Fisher ratio of an LDA direction (fit on one half, measured on the other)."""
    idx = np.random.default_rng(seed).permutation(len(z))
    h = len(z) // 2
    out = []
    for fit, test in ((idx[:h], idx[h:]), (idx[h:], idx[:h])):
        lf = lab[fit]
        if min((lf == 0).sum(), (lf == 1).sum()) < 3:
            return 0.0
        w = np.linalg.solve(_within_cov(z[fit], lf, ridge), z[fit][lf == 1].mean(0) - z[fit][lf == 0].mean(0))
        out.append(_fisher(z[test] @ w, lab[test]))
    return float(np.mean(out))


def _radius(dist: np.ndarray, k: float) -> float:
    med = float(np.median(dist))
    mad = float(np.median(np.abs(dist - med)))
    return med + k * 1.4826 * max(mad, 1e-6)


def _candidates(x: np.ndarray, z: np.ndarray, p: FitParams) -> dict[str, np.ndarray]:
    out: dict[str, np.ndarray] = {}
    for k in p.knn:
        for dims in p.spectral_dims:
            v = spectral_embedding(x, k, dims)
            out[f"spectral_k{k}_d{dims}"] = kmeans(v, 2, n_init=p.n_init, max_iter=p.max_iter, seed=p.seed)[1]
    out["pca8_kmeans"] = kmeans(z[:, :8], 2, n_init=p.n_init, max_iter=p.max_iter, seed=p.seed)[1]
    return out


def fit_team_model(emb: np.ndarray, *, embedder: str, embedder_sha256: str | None = None,
                   params: FitParams | None = None) -> TeamModel:
    """Fit on shirt embeddings [N, D] (L2-normalised); raises ``TeamSeparationFailed``."""
    p = params or FitParams()
    x = np.asarray(emb, np.float64)
    if len(x) < p.min_crops:
        raise TeamSeparationFailed("too_few_crops", f"{len(x)} crops < {p.min_crops}", {"crops": len(x)})
    x = x / (np.linalg.norm(x, axis=1, keepdims=True) + 1e-12)
    mean, comp, evr = pca(x, p.n_components)
    z = (x - mean) @ comp.T
    scores: dict[str, float] = {}
    shares: dict[str, float] = {}
    cands = _candidates(x, z, p)
    for name, lab in cands.items():
        shares[name] = float(min(lab.mean(), 1 - lab.mean()))
        scores[name] = cv_fisher(z, lab, ridge=p.ridge, seed=p.seed) if shares[name] >= p.min_share else 0.0
    best = max(scores, key=lambda n: scores[n])
    lab = cands[best]
    stats: dict[str, Any] = {
        "crops": len(x), "split": best, "cv_fisher": round(scores[best], 3),
        "candidates": {n: {"cv_fisher": round(scores[n], 3), "share": round(shares[n], 3)} for n in cands},
        "pca_explained": round(float(evr.sum()), 4), "n_components": int(comp.shape[0]),
        "params": {"outlier_k": p.outlier_k, "min_share": p.min_share, "min_fisher": p.min_fisher,
                   "min_crops": p.min_crops, "ridge": p.ridge, "seed": p.seed},
    }
    if max(shares.values()) < p.min_share:
        stats["min_share"] = round(max(shares.values()), 4)
        raise TeamSeparationFailed("imbalanced", f"no split gives the smaller team ≥ {p.min_share:.0%}", stats)
    if scores[best] < p.min_fisher:
        raise TeamSeparationFailed("not_separable", f"cross-validated Fisher {scores[best]:.2f} < {p.min_fisher}",
                                   stats)

    # whitened space: PCA rows → within-team covariance^(-1/2) (nearest centroid = LDA), refit once on inliers
    # pass 0 in plain PCA space drops gross outliers (referees) before they inflate the within-team covariance
    inlier = np.ones(len(z), bool)
    white = np.eye(z.shape[1])
    for it in range(3):
        if it:
            evals, evecs = np.linalg.eigh(_within_cov(z[inlier], lab[inlier], p.ridge))
            white = (evecs / np.sqrt(np.maximum(evals, 1e-12))).T        # [k, k]
        zw = z @ white.T
        cent = np.array([zw[inlier & (lab == j)].mean(0) for j in (0, 1)])
        d = np.linalg.norm(zw[:, None, :] - cent[None], axis=2)
        lab = d.argmin(1)
        own = d[np.arange(len(zw)), lab]
        radius = np.array([_radius(own[(lab == j) & inlier], p.outlier_k) for j in (0, 1)])
        inlier = own <= radius[lab]
    # canonical order: team 0 = the larger team (names / colours are confirmed by the user later)
    sizes = np.array([(inlier & (lab == j)).sum() for j in (0, 1)])
    if sizes[1] > sizes[0]:
        cent, radius, lab, sizes = cent[::-1].copy(), radius[::-1].copy(), 1 - lab, sizes[::-1].copy()
    share = float(sizes.min() / max(sizes.sum(), 1))
    stats.update({
        "inliers": int(inlier.sum()), "outliers": int((~inlier).sum()), "cluster_sizes": [int(s) for s in sizes],
        "min_share": round(share, 4), "centroid_distance": round(float(np.linalg.norm(cent[0] - cent[1])), 4),
        "silhouette": round(silhouette(zw[inlier], lab[inlier], sample=p.silhouette_sample, seed=p.seed), 4),
    })
    if share < p.min_share:
        raise TeamSeparationFailed("imbalanced", f"smaller team has {share:.0%} of the inliers", stats)
    return TeamModel(embedder=embedder, embedder_sha256=embedder_sha256, mean=mean, components=white @ comp,
                     centroids=cent, radius=radius, stats=stats)


def representative(model: TeamModel, emb: np.ndarray, n: int = 3) -> dict[int, list[int]]:
    """Indices of the ``n`` crops closest to each centroid (the UI's "is this team A?" samples)."""
    d = model.distances(emb)
    lab = model.predict(emb)
    out: dict[int, list[int]] = {}
    for j in (0, 1):
        idx = np.flatnonzero(lab == j)
        out[j] = [int(i) for i in idx[np.argsort(d[idx, j], kind="stable")[:n]]]
    return out


def vote(labels: Sequence[int]) -> int:
    """Majority team of a track's per-crop labels; -1 wins only if it is the plurality (ties → -1)."""
    if not len(labels):
        return NO_TEAM
    vals, counts = np.unique(np.asarray(labels, np.int64), return_counts=True)
    top = counts.max()
    winners = vals[counts == top]
    return int(winners[0]) if len(winners) == 1 else NO_TEAM
