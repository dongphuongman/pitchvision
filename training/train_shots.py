"""Train / evaluate the S2b shot classifier (M1-CV-06) and export it as a ``pv-gbm/v1`` JSON for cv_core.

    uv run --project training python training/train_shots.py \
        --sp09-dir spikes/results/sp09 --golden-dir /path/to/data/golden            # colour + D-FINE persons
    uv run --project training python training/train_shots.py --no-detector         # colour-only fallback

Steps
1. Ground truth: SP-09 labels (``eval/pveval/shots.sp09_samples``: 855 frames, 19 golden matches, binary
   ``main_wide`` / ``non_wide``).
2. Frames: decoded from ``<golden>/raw/<video>.mp4`` with ``cv_core.io.VideoReader`` at the label time on the
   25 fps / 1280 px timeline (what S2b sees on normalized.mp4), features from ``cv_core.shots`` — the same code
   path as inference. Cached in ``--out-dir/features_<tag>.npz``.
3. Leave-one-video-out CV with ``HistGradientBoostingClassifier`` (SP-09 settings); every fold model is exported to
   ``pv-gbm/v1`` and the *exported numpy model* produces the CV predictions (so the eval covers the exporter).
   Writes ``--out-dir/shot_preds_lovo_<tag>.json`` (pv-shot-samples/v1) → score with ``eval/run_shots.py --pred``.
   ``--export-folds`` also writes each fold model (``folds_<tag>/<video>.json``) for the timeline eval
   (``eval/run_shots.py --timeline``), so that eval is leave-video-out too.
4. Final model on all labels → ``<model-dir>/shots-gbm-<tag>-v<version>.json``; prints sha256 for models/registry.yaml.

4-class (later): pass ``--labels my_labels.json`` in pv-shot-samples/v1 with labels in
``main_wide|close_up|replay|other`` (SoccerNet camera-shot classes mapped: main camera center/left/right →
main_wide; close-up player/coach/referee, spectator → close_up/other; public/inside-goal/goal-line → other;
replays carry SoccerNet's ``replay`` flag → replay). The trainer then fits a K-class softmax HGB and the exported model
has ``classes`` = those labels and an identity ``class_map``; cv_core needs no change. NB SoccerNet is
research-only (G-LEGAL): train production weights on our own labels unless legal clears it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.model_selection import LeaveOneGroupOut

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "eval"))

from cv_core.detect import DFineDetector  # noqa: E402
from cv_core.io import VideoReader  # noqa: E402
from cv_core.runtime import Runtime  # noqa: E402
from cv_core.shots import ShotClassifier, feature_names  # noqa: E402
from cv_core.shots.gbm import GbmModel, Tree  # noqa: E402
from pveval.formats import ShotSample, read_shot_samples, write_shot_samples  # noqa: E402
from pveval.metrics import NON_WIDE, WIDE  # noqa: E402
from pveval.shots import sp09_samples  # noqa: E402

DETECTOR = "dfine-m-obj2coco-640"
HGB_PARAMS: dict[str, Any] = {"max_depth": 3, "max_iter": 200, "learning_rate": 0.08, "random_state": 0}


def default_model_dir() -> Path:
    return Path(os.environ.get("PITCHVISION_MODEL_DIR", Path.home() / ".cache" / "pitchvision" / "models"))


# --- HGB → pv-gbm/v1 -------------------------------------------------------------------------------------------------
def export_hgb(clf: HistGradientBoostingClassifier, features: tuple[str, ...], classes: list[str], *, name: str,
               version: str, detector: str | None, class_map: dict[str, str], meta: dict[str, Any]) -> GbmModel:
    if getattr(clf, "_is_categorical_remapped", None) is not None and np.any(clf._is_categorical_remapped):
        raise ValueError("categorical features are not supported by pv-gbm/v1")
    trees = []
    for it in clf._predictors:
        row = []
        for pred in it:
            nd = pred.nodes
            leaf = nd["is_leaf"].astype(bool)
            row.append(Tree(
                feature=np.where(leaf, 0, nd["feature_idx"].astype(np.int64)).astype(np.int32),
                threshold=np.asarray(nd["num_threshold"], np.float64),
                left=np.where(leaf, -1, nd["left"].astype(np.int64)).astype(np.int32),
                right=np.where(leaf, -1, nd["right"].astype(np.int64)).astype(np.int32),
                value=np.asarray(nd["value"], np.float64),
                nan_left=np.asarray(nd["missing_go_to_left"], bool),
            ))
        trees.append(tuple(row))
    baseline = np.asarray(clf._baseline_prediction, np.float64).reshape(-1)
    return GbmModel(name=name, version=version, classes=tuple(classes), features=features, baseline=baseline,
                    trees=tuple(trees), class_map=class_map, detector=detector, meta=meta)


def check_export(clf: HistGradientBoostingClassifier, model: GbmModel, X: np.ndarray) -> float:
    ref = clf.predict_proba(X)
    got = model.predict_proba(X)
    err = float(np.abs(ref - got).max())
    if err > 1e-6:
        raise AssertionError(f"exported model deviates from sklearn by {err:.2e}")
    return err


# --- features --------------------------------------------------------------------------------------------------------
def extract(samples: list[ShotSample], golden_dir: Path, clf_feat: ShotClassifier, cache: Path,
            long_side: int) -> np.ndarray:
    keys = [f"{s.video}@{s.t_ms}" for s in samples]
    if cache.is_file():
        z = np.load(cache, allow_pickle=False)
        if list(z["keys"]) == keys:
            print(f"features: cached {cache}", file=sys.stderr)
            return np.asarray(z["X"])
    X, t0 = [], time.perf_counter()
    for i, s in enumerate(samples, 1):
        path = golden_dir / "raw" / f"{s.video}.mp4"
        t = s.t_ms / 1000
        fr = next(iter(VideoReader(path, t, t + 0.04, target_fps=25, long_side=long_side)), None)
        if fr is None:
            raise SystemExit(f"no frame at {s.video} {t}s")
        X.append(clf_feat.features([fr.image])[0])
        if i % 100 == 0:
            print(f"  features {i}/{len(samples)} ({time.perf_counter() - t0:.0f}s)", file=sys.stderr)
    Xa = np.stack(X).astype(np.float64)
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.savez(cache, X=Xa, keys=np.array(keys))
    return Xa


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sp09-dir", type=Path, default=REPO / "spikes" / "results" / "sp09")
    ap.add_argument("--labels", type=Path, help="pv-shot-samples/v1 GT instead of SP-09 (e.g. 4-class labels)")
    ap.add_argument("--golden-dir", type=Path,
                    default=Path(os.environ.get("PITCHVISION_GOLDEN_DIR", REPO / "data" / "golden")))
    ap.add_argument("--no-detector", action="store_true", help="colour features only (no D-FINE)")
    ap.add_argument("--detector", type=Path, help=f"detector ONNX (default <model-dir>/{DETECTOR}.onnx)")
    ap.add_argument("--provider", default="ort_coreml", help="Runtime provider preference for the detector")
    ap.add_argument("--long-side", type=int, default=1280, help="decode size (normalized.mp4 = 1280)")
    ap.add_argument("--model-dir", type=Path, default=default_model_dir())
    ap.add_argument("--out-dir", type=Path, default=REPO / "training" / "out")
    ap.add_argument("--version", default="1")
    ap.add_argument("--export-folds", action="store_true")
    a = ap.parse_args()

    with_persons = not a.no_detector
    tag = "det" if with_persons else "colour"
    samples = read_shot_samples(a.labels) if a.labels else sp09_samples(a.sp09_dir)[0]
    feats = feature_names(with_persons)
    classes = sorted({s.label for s in samples}, key=lambda c: (c != WIDE, c))
    binary = set(classes) == {WIDE, NON_WIDE}
    class_map = {NON_WIDE: "other"} if binary else {}
    print(f"{len(samples)} samples, {len({s.video for s in samples})} videos, classes {classes}, "
          f"features {len(feats)} ({tag})", file=sys.stderr)

    detector = None
    if with_persons:
        det_path = a.detector or a.model_dir / f"{DETECTOR}.onnx"
        detector = DFineDetector(Runtime.select(prefer=a.provider).model(str(det_path)), conf=0.35)
        print(f"detector {det_path.name} on {detector.model.provider.value}", file=sys.stderr)
    # a dummy model just to reuse ShotClassifier.features (exact inference feature path)
    stub = GbmModel(name="stub", version="0", classes=(WIDE, NON_WIDE), features=feats, baseline=np.zeros(1),
                    trees=(), class_map={NON_WIDE: "other"})
    X = extract(samples, a.golden_dir, ShotClassifier(stub, detector), a.out_dir / f"features_{tag}.npz",
                a.long_side)
    y = np.array([classes.index(s.label) for s in samples])
    groups = np.array([s.video for s in samples])
    meta_base = {"trained_on": "SP-09 labels (19 golden matches, 855 frames)" if not a.labels else str(a.labels.name),
                 "hgb": HGB_PARAMS, "decode_long_side": a.long_side, "detector_conf": 0.35 if with_persons else None}

    # leave-one-video-out with the exported numpy model
    preds: list[ShotSample] = []
    fold_dir = a.out_dir / f"folds_{tag}"
    for tr, te in LeaveOneGroupOut().split(X, y, groups):
        clf = HistGradientBoostingClassifier(**HGB_PARAMS).fit(X[tr], y[tr])
        vid = groups[te][0]
        m = export_hgb(clf, feats, classes, name=f"shots-gbm-{tag}-lovo-{vid}", version=a.version,
                       detector=DETECTOR if with_persons else None, class_map=class_map,
                       meta={**meta_base, "held_out": vid})
        check_export(clf, m, X[te])
        p = m.predict_proba(X[te])
        for i, row in zip(te, p, strict=True):
            k = int(row.argmax())
            preds.append(ShotSample(samples[i].video, samples[i].t_ms, classes[k], float(row[k])))
        if a.export_folds:
            fold_dir.mkdir(parents=True, exist_ok=True)
            m.save(fold_dir / f"{vid}.json")
    pred_path = a.out_dir / f"shot_preds_lovo_{tag}.json"
    write_shot_samples(pred_path, preds, model=f"shots-gbm-{tag} leave-one-video-out (exported pv-gbm/v1)")
    gt = {(s.video, s.t_ms): s.label for s in samples}
    acc = float(np.mean([p.label == gt[(p.video, p.t_ms)] for p in preds]))
    print(f"LOVO accuracy {acc:.4f} → {pred_path}", file=sys.stderr)

    # final model
    clf = HistGradientBoostingClassifier(**HGB_PARAMS).fit(X, y)
    name = f"shots-gbm-{tag}"
    final = export_hgb(clf, feats, classes, name=name, version=a.version,
                       detector=DETECTOR if with_persons else None, class_map=class_map,
                       meta={**meta_base, "lovo_accuracy": round(acc, 4), "n_samples": len(samples)})
    err = check_export(clf, final, X)
    a.model_dir.mkdir(parents=True, exist_ok=True)
    out = a.model_dir / f"{name}-v{a.version}.json"
    final.save(out)
    print(json.dumps({"model": str(out), "sha256": sha256(out), "bytes": out.stat().st_size,
                      "export_max_abs_err": err, "lovo_accuracy": acc}, indent=1))


if __name__ == "__main__":
    main()
