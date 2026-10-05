# Model card — `shots-gbm-det` / `shots-gbm-colour` (S2b shot classifier, M1-CV-06)

| | |
|---|---|
| Task | Per-frame shot type for S2b: `main_wide` vs rest (binary now; 4-class-ready format) |
| Model | scikit-learn `HistGradientBoostingClassifier` (max_depth 3, 200 iterations, lr 0.08 — SP-09 settings), exported to `pv-gbm/v1` JSON and evaluated in pure numpy (`cv_core.shots.gbm`) — no sklearn at runtime, export checked to ≤ 1e-6 vs `predict_proba` |
| Features | `cv_core.shots.features` on the frame resized to 640 px: grass share (all / lower ⅔ / top ⅓), pitch-line pixels, Canny edge density, saturation / value mean & std, grass hue spread; `-det` adds D-FINE-M persons (conf ≥ 0.35): count, max / median / mean height ÷ frame height, # small (< 10 %) |
| Detector | `dfine-m-obj2coco-640` (Apache-2.0) via `cv_core.runtime` — replaces SP-09's YOLO11 (AGPL) |
| Labels | SP-09: 855 frames, one per 20 s of 19 golden 15-min segments, binary wide / non-wide, labelled by Claude (PO spot-check pending) |
| Output | `classes: [main_wide, non_wide]`, `class_map: {non_wide: other}` → `ShotType` |
| Build | `uv run --project training python training/train_shots.py [--no-detector] --export-folds` |

## Evaluation (leave-one-video-out, exported numpy model; `eval/results/`)

| Model | Frames | Accuracy | main_wide precision | main_wide recall | Worst matches |
|---|---|---|---|---|---|
| `shots-gbm-det` per frame | 855 | **98.1 %** | 99.3 % | 98.0 % | EYj9Wbo0n44 (single-camera youth) 86.7 %, UKFYHHQzTvM (sun/shadow) 91.1 % |
| `shots-gbm-colour` per frame | 855 | 87.5 % | 94.0 % | 88.0 % | below the 95 % exit criterion → fallback only |
| `shots-gbm-det` full S2b timeline (cuts + per-second classification + smoothing + merge < 1 s, leave-video-out fold models, raw golden videos at 25 fps) | 855 | **97.9 %** | 98.7 % | 98.4 % | EYj9Wbo0n44 91.1 %, UKFYHHQzTvM 93.3 %, then 95.6 % (MZjaWGwbtEc, TyCCa3tyhZw, _FPE6jjJQ8M) |

Worst groups — per frame (det): youth 96.4 %, lighting=day 93.3 %, 25 fps 95.6 %; timeline: lighting=day 94.4 %, 25 fps 95.6 %, north_america 96.7 %.

S2b throughput (M4, 3 processes in parallel, CoreML detector): 70 fps per process on a 15-min video (22.5k frames, 320 s:
decode+resize 138 s, classification of 900 frames 162 s, cut histograms 20 s). A 90' match ≈ 32 min per process on a Mac;
on the GPU worker (NVDEC + TensorRT detector) classification is ~1 % of S4's detector load.
SP-09 reference (YOLO11 person features, 5-fold group CV): 99.1 %.

## Limits / next
- Binary only: `close_up` / `replay` / crowd / graphics all map to `other`. Replays from the main camera are labelled
  non-wide in SP-09 but can look like wide play frame-by-frame (not yet quantified); replay detection is M2.
- Labels are point samples every 20 s: the per-second exit criterion is approximated by per-frame accuracy at those
  samples and by scoring the full timeline at the same timestamps.
- Single-camera youth footage (EYj9Wbo0n44) has low, close camera angles: more such matches in the golden set needed.
