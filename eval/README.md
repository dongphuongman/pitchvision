# eval — golden set v0 + metric scripts (M1-CV-03)

Plain scripts (not a workspace member), run from the repo root with `uv run python eval/<script>.py`.
`pveval/` holds the pure metric library; tests live in `eval/tests/` (root pytest testpaths).

| File | What |
|---|---|
| `golden.json` | 19 golden matches + strata (category, region, fps_class, lighting, source_quality, split). Regenerate with `build_golden.py` |
| `build_golden.py` | `data/golden/manifest.json` → `golden.json` (curated region / lighting / hard-case tags live in `pveval/golden.py`) |
| `run_shots.py` | Shot classification vs SP-09 labels; green-ratio baseline or `--pred` file |
| `run_detection.py` | AP@0.5 per class + mAP@50 from COCO GT + COCO results |
| `run_teams.py` | Team accuracy (0/1 permutation-invariant per match) |

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

**Homography** — `pveval.metrics.reprojection_errors(H, src_px, dst_m)` + `homography_report(per_frame_mean_errors, threshold_m)`;
the label file (pitch keypoints per frame, CVAT points) is defined with M1-CV-05.
