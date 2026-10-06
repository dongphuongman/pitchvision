"""Team split: cv_core's team model (spectral split + whitened-LDA predictor, numpy only) vs SP-04's
UMAP-3D → KMeans(2) on the same SigLIP crops (M1-CV-09).

Step 1 — crops + embeddings with the production code (cv_core D-FINE + grass filter + SigLIP ONNX), SP-04's frame
sampling (every 20 s from t = 5 s, main_wide per SP-09 labels, ≤ 30 frames per match):

    uv run python eval/teams_vs_umap.py collect --out-dir /tmp/pv_teams [--videos ...] [--provider ort_coreml]

Step 2 — compare (needs scikit-learn + umap-learn, so it runs in the training env):

    uv run --project training --with umap-learn python eval/teams_vs_umap.py compare --out-dir /tmp/pv_teams \
        --report eval/results/teams_m1cv09_vs_umap.json

Per match: agreement of our labels with the UMAP labels on our inliers (permutation-invariant), the agreement of
plain PCA-8 → KMeans (the first M1-CV-09 attempt) for reference, our cross-validated Fisher ratio, outlier share,
and a montage (UMAP A | B, ours 0 | 1 | -1) in ``--out-dir/montage_vs_umap.jpg`` for the visual check SP-04 used
(its ~98 % was a visual score). Frames / crops stay in ``--out-dir`` (outside the repo).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
NONWIDE = ROOT / "spikes" / "results" / "sp09" / "labels_nonwide.json"


def _golden() -> Path:
    return Path(os.environ.get("PITCHVISION_GOLDEN_DIR", ROOT / "data" / "golden"))


def collect(a: argparse.Namespace) -> None:
    from cv_core.detect import DFineDetector
    from cv_core.io.video import VideoReader
    from cv_core.registry import ModelRegistry
    from cv_core.runtime import Runtime
    from cv_core.teams import SiglipEmbedder
    from cv_core.teams.calibrate import collect_crops

    nonwide = json.loads(NONWIDE.read_text())
    reg = ModelRegistry.load()
    det = DFineDetector(Runtime.select(a.provider).model(str(reg.resolve("dfine-m-obj2coco-640"))), conf=0.35)
    emb = SiglipEmbedder(Runtime.select(a.embedder_provider).model(str(reg.resolve("siglip-base-p16-224"))))
    videos = a.videos or sorted(k for k in nonwide if k != "_doc")
    for vid in videos:
        out = a.out_dir / f"{vid}.npz"
        src = _golden() / "raw" / f"{vid}.mp4"
        if out.exists() or not src.exists():
            continue
        ks = [k for k in range(45) if k not in nonwide.get(vid, [])][: a.frames]
        frames = []
        for k in ks:
            t = 5 + 20 * k
            for fr in VideoReader(src, t, t + 0.04, target_fps=25, long_side=1280):
                frames.append((fr.idx, fr.image))
        samples = collect_crops(frames, det)
        e = emb([s.crop for s in samples])
        thumbs = np.stack([cv2.resize(s.crop, (24, 32), interpolation=cv2.INTER_AREA) for s in samples])
        np.savez_compressed(out, emb=e, thumbs=thumbs, frame_idx=np.array([s.frame_idx for s in samples]))
        print(f"{vid}: {len(frames)} frames, {len(samples)} crops", flush=True)


def _agree(a: np.ndarray, b: np.ndarray) -> float:
    m = float((a == b).mean())
    return max(m, 1 - m)


def _row(label: str, groups: list[np.ndarray], thumbs: np.ndarray, per: int = 10) -> np.ndarray:
    tiles = [np.full((32, 110, 3), 0, np.uint8)]
    cv2.putText(tiles[0], label[:16], (2, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 255, 255), 1, cv2.LINE_AA)
    rng = np.random.default_rng(0)
    for g in groups:
        pick = rng.permutation(g)[:per]
        tiles += [thumbs[i] for i in pick] + [np.zeros((32, 24, 3), np.uint8)] * (per - len(pick))
        tiles.append(np.full((32, 6, 3), 255, np.uint8))
    return cv2.hconcat(tiles)


def compare(a: argparse.Namespace) -> None:
    import umap  # type: ignore[import-not-found]
    from cv_core.teams import TeamSeparationFailed, fit_team_model, kmeans, pca
    from sklearn.cluster import KMeans  # type: ignore[import-not-found]

    rows: dict[str, Any] = {}
    montage: list[np.ndarray] = []
    for f in sorted(a.out_dir.glob("*.npz")):
        vid = f.stem
        d = np.load(f)
        e, thumbs = d["emb"].astype(np.float64), d["thumbs"]
        lab_u = KMeans(2, n_init=10, random_state=0).fit_predict(
            umap.UMAP(n_components=3, random_state=0).fit_transform(e))
        mean, comp, _ = pca(e, 8)
        lab_p = kmeans((e - mean) @ comp.T, 2, seed=0)[1]
        r: dict[str, Any] = {"crops": len(e), "umap_share": round(float(min(lab_u.mean(), 1 - lab_u.mean())), 3),
                             "pca8_kmeans_agree_umap": round(_agree(lab_p, lab_u), 3)}
        try:
            m = fit_team_model(e, embedder="siglip")
        except TeamSeparationFailed as ex:
            r["failed"] = ex.reason
            r.update({k: ex.stats.get(k) for k in ("cv_fisher", "split")})
            rows[vid] = r
            print(vid, json.dumps(r), flush=True)
            continue
        lab = m.predict(e)
        inl = lab >= 0
        r.update({"split": m.stats["split"], "cv_fisher": m.stats["cv_fisher"], "min_share": m.stats["min_share"],
                  "outlier_share": round(float((~inl).mean()), 3),
                  "agree_umap_on_inliers": round(_agree(lab[inl], lab_u[inl]), 3)})
        rows[vid] = r
        print(vid, json.dumps(r), flush=True)
        montage.append(cv2.hconcat([
            _row(f"{vid} umap", [np.flatnonzero(lab_u == 0), np.flatnonzero(lab_u == 1)], thumbs),
            _row("ours 0|1|-1", [np.flatnonzero(lab == j) for j in (0, 1, -1)], thumbs)]))
    if montage:
        w = max(x.shape[1] for x in montage)
        montage = [cv2.copyMakeBorder(x, 0, 2, 0, w - x.shape[1], cv2.BORDER_CONSTANT) for x in montage]
        cv2.imwrite(str(a.out_dir / "montage_vs_umap.jpg"), cv2.vconcat(montage), [cv2.IMWRITE_JPEG_QUALITY, 90])
    ok = [r for r in rows.values() if "agree_umap_on_inliers" in r]
    summary: dict[str, Any] = {
        "videos": len(rows), "failed": sorted(v for v, r in rows.items() if "failed" in r),
        "agree_umap_median": float(np.median([r["agree_umap_on_inliers"] for r in ok])) if ok else None,
        "agree_umap_min": min((r["agree_umap_on_inliers"] for r in ok), default=None),
        "agree_umap_ge_0.95": sum(r["agree_umap_on_inliers"] >= 0.95 for r in ok),
        "pca8_kmeans_agree_umap_median": float(np.median([r["pca8_kmeans_agree_umap"] for r in rows.values()])),
        "pca8_kmeans_agree_umap_lt_0.7": sum(r["pca8_kmeans_agree_umap"] < 0.7 for r in rows.values()),
        "cv_fisher_min": min((r["cv_fisher"] for r in ok), default=None),
        "outlier_share_median": float(np.median([r["outlier_share"] for r in ok])) if ok else None,
    }
    print(json.dumps(summary, indent=1))
    if a.report:
        a.report.write_text(json.dumps({"format": "pv-teams-vs-umap/v1", "summary": summary, "per_match": rows},
                                       indent=1))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["collect", "compare"])
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--videos", nargs="*")
    ap.add_argument("--frames", type=int, default=30)
    ap.add_argument("--provider", default=None)
    ap.add_argument("--embedder-provider", default="ort_cpu")
    ap.add_argument("--report", type=Path)
    a = ap.parse_args()
    a.out_dir = a.out_dir.resolve()
    if ROOT in a.out_dir.parents:
        sys.exit("--out-dir must be outside the repo")
    a.out_dir.mkdir(parents=True, exist_ok=True)
    collect(a) if a.step == "collect" else compare(a)


if __name__ == "__main__":
    main()
