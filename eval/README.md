# eval — golden set v0 + metric scripts (M1-CV-03)

Plain scripts (not a workspace member), run from the repo root with `uv run python eval/<script>.py`.
`pveval/` holds the pure metric library; tests live in `eval/tests/` (root pytest testpaths).

| File | What |
|---|---|
| `golden.json` | 19 golden matches + strata (category, region, fps_class, lighting, source_quality, split). Regenerate with `build_golden.py` |
| `build_golden.py` | `data/golden/manifest.json` → `golden.json` (curated region / lighting / hard-case tags live in `pveval/golden.py`) |
| `run_shots.py` | Shot classification vs SP-09 labels; green-ratio baseline, `--pred` file, or `--timeline-models DIR` (full S2b `cv_core.shots.segment_video` per video with its leave-out model) |
| `results/` | Committed eval summaries (JSON, no frames): `shots_m1cv06_*.json` = M1-CV-06 shot classifier |
| `run_detection.py` | AP@0.5 per class + mAP@50 from COCO GT + COCO results |
| `run_teams.py` | Team accuracy (0/1 permutation-invariant per match) |
| `run_homography.py` | Pitch calibration (M1-CV-05/18): line-alignment `h_quality` on golden frames + (with GT) keypoint / field error in metres; `--model` (cv_core pipeline) or `--pred`; `--sp09` main_wide frames; `--render-dir` overlays (refuses paths inside the repo) |
| `teams_vs_umap.py` | Team split (M1-CV-09): cv_core team model (spectral split + whitened LDA) vs SP-04 UMAP→KMeans on the same SigLIP crops (`collect` in the main env, `compare` in the training env `--with umap-learn`); montage for the visual check |
| `run_pipeline_w3.py` | W3 smoke eval (M1-CV-08/09/10): S2 → S2b → segment plan → S3 → S4 on the first minutes of golden matches: segments, team split + crop grids, tracking fragmentation vs SP-05, wall time / fps per stage |
| `bench_infer.py` | S4 throughput on one segment per detector provider (CPU EP vs CoreML EP on a Mac): detect / track / team-vote split |

Every script prints per match, per stratum (pooled, not mean of means) and overall, and writes JSON.
Raw videos are **not** in the repo: set `PITCHVISION_GOLDEN_DIR` (default `<repo>/data/golden`);
SP-09 frames: `--sp09-dir` / `PITCHVISION_SP09_DIR` (default `<repo>/spikes/results/sp09`).
Outputs go to `eval/out/` (gitignored).

## Label formats

**Shots** — GT and predictions, `t_ms` on the source video timeline:

```json
{"format": "pv-shot-samples/v1", "samples": [{"video": "BukNW8GRI5A", "t_ms": 5000, "label": "main_wide", "conf": 0.9}]}
```

GT labels are `main_wide | close_up | replay | other`, or the binary `main_wide | non_wide` (SP-09);
with binary GT, predicted non-wide types collapse to `non_wide`. Predictions may instead be
`{"format": "pv-shot-timelines/v1", "timelines": {"<video>": <ShotTimeline JSON>}}` (packages/schemas),
sampled at the GT timestamps.

**Detection** — GT is a COCO instances file (CVAT export "COCO 1.0"): categories named
`player`, `goalkeeper`, `referee`, `ball` (mapped to AD §6.5 classes 0–3), image `file_name`
`{video_id}/{frame_idx:06d}.jpg` with `frame_idx` on the normalised 25 fps timeline (`cv_core.io.VideoReader`).
Predictions are a COCO results list `[{"image_id", "category_id", "bbox": [x, y, w, h], "score"}]`
with the GT's ids. Matching is COCO-style greedy at IoU ≥ 0.5, 101-point AP, no maxDets cap.

**Teams** — GT and predictions:

```json
{"format": "pv-team-labels/v1", "items": [{"video": "BukNW8GRI5A", "frame_idx": 2500, "obj": "17", "team": 0}]}
```

`obj` identifies the GT box (CVAT annotation id or track id); `team` is 0/1, or -1 for referees /
not a team player (skipped). The predictor reports a team per GT box; a missing prediction counts as wrong.

**Homography** — GT pitch keypoints (CVAT / Label Studio exports convert with `training/keypoints/convert.py`):

```json
{"format": "pv-pitch-keypoints/v1", "template": "fifa-105x68",
 "pitch_size": {"BukNW8GRI5A": [105, 68]},
 "frames": [{"video": "BukNW8GRI5A", "t_ms": 65000, "width": 1280, "height": 720, "image": "BukNW8GRI5A_0065000.jpg",
             "keypoints": [[13, 731.2, 252.0], [14, 728.4, 323.5, 1]]}]}
```

`keypoints` = `[idx, x, y, v?]`: `idx` is the `cv_core.pitch.template` index (32 points, roboflow order, table in
`cv_core/pitch/template.py`), x/y pixels of a `width × height` frame on the normalised timeline (`t_ms`, or
`frame_idx` at 25 fps), `v` 2 visible / 1 occluded (default 2). Only in-frame points are listed. `pitch_size`
(optional, per video) when the venue is not 105 × 68 m. Labelling rules: `training/keypoints/README.md`.

Predictions `{"format": "pv-homographies/v1", "frames": [{"video", "t_ms", "width", "height", "H", "h_quality"?,
"reason"?}]}` with `H` the 3×3 **image → pitch metres** matrix (null = no homography); written by
`run_homography.py --write-pred`.

Metrics (`pveval.metrics`): `h_quality` = line alignment (`cv_core.pitch.lines`: projected template vs detected white
lines, F0.5 of per-marking precision and recall; frames without H = 0) — never the fit residual on the keypoints,
which SP-03 showed to be meaningless; `reprojection_errors` at GT keypoints (m); `field_errors` = metre error vs the
GT homography (fit to ≥ 4 well-spread GT points) over a pixel grid of the visible pitch, i.e. the error a player's
foot gets; `homography_report(..., 1.0)` → share of frames < 1 m (M1-CV-18 gate ≥ 85 %, no-H frames fail);
`quality_report` → h_quality histogram / shares.

Baseline (internal, spike roboflow model, no frames): `results/homography_m1cv05_baseline_rf_spike.json`.
