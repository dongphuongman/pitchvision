# Pitch keypoints — YOLOv11-pose fine-tune (M1-CV-05 / M1-CV-19 prep; the run needs an NVIDIA GPU)

Goal: a YOLOv11-pose model predicting the **32 pitch keypoints of `cv_core.pitch.template`** (roboflow/sports index
order, metres on a FIFA 105 × 68 m pitch) on broadcast main-camera frames, exported to ONNX and run in cv-core by
`cv_core.pitch.PitchKeypointModel` → `estimate_homography` → line-alignment `h_quality`.

**Licence boundary.** Ultralytics is AGPL-3.0. PO decision 05/10/2026: we use it under an **Ultralytics Enterprise
licence, signed before public beta** (before any customer-facing run). Until then dev/fine-tune only. To keep the
boundary visible this folder is its **own uv project** (`pyproject.toml`, own `uv.lock`, `ultralytics==8.4.173`
pinned) — not a workspace member and not part of `training/`'s env. `cv-core` never imports `ultralytics`; it only
decodes the exported ONNX graph through `cv_core.runtime`.

| File | What |
|---|---|
| `convert.py` | CVAT XML / Label Studio JSON / SoccerNet calibration → `pv-pitch-keypoints/v1`; `frames` (golden frames for labelling); `yolo` → YOLO-pose dataset (split by video, `flip_idx`, completion from H) |
| `config.yaml` | Base `yolo11m-pose.pt`, imgsz 960, AdamW, no mosaic/mixup, flips with `flip_idx`, pose/kobj gains |
| `train.py` | Launches `YOLO(base).train(**config)` with `--set key=value` overrides; prints the export command |
| `export_onnx.py` | `.pt` → ONNX (fixed input, static batch 1, opset 17, no NMS) → `$PITCHVISION_MODEL_DIR`; parity vs Ultralytics on the same graph; CPU + CoreML latency; sha256 for the registry |
| `smoke_test.py` | CPU, ~1 min: synthetic frames rendered from the template → all converters → completion → YOLO dataset → 2 epochs YOLO11n-pose → ONNX → cv_core decode |

`pv-pitch-keypoints/v1` is the same format `eval/run_homography.py --labels` reads (eval/README.md), so one CVAT
export feeds both training and the golden calibration eval (M1-CV-18) — keep **eval frames out of training** (split
by video; the golden `split: test` matches are eval-only).

## Smoke test (Mac, done)
```bash
uv sync --project training/keypoints
uv run --project training/keypoints python training/keypoints/smoke_test.py
```
2026-10-06, M4, CPU: converters exact (CVAT, Label Studio; SoccerNet lines → intersections 0.00 px) → completion
from H → YOLO dataset → 2 epochs YOLO11n-pose (finite losses) → ONNX → `PitchKeypointModel` decodes 32 points. ✓
MPS is not used: backward passes are unreliable on torch 2.14 (same as D-FINE) — CPU locally, CUDA for real runs.

## GPU run recipe (RunPod RTX 4090 24 GB)
```bash
# pod: PyTorch 2.x CUDA 12.x template, 50 GB volume at /workspace
git clone <repo> /workspace/pitchvision && cd /workspace/pitchvision
pip install uv && uv sync --project training/keypoints
export PITCHVISION_MODEL_DIR=/workspace/models K=training/keypoints

# 1) labels → pv-pitch-keypoints/v1 (frames under $K/data/, gitignored)
uv run --project $K python $K/convert.py cvat /workspace/cvat/annotations.xml --out $K/data/kp/ours.json
uv run --project $K python $K/convert.py frames --labels $K/data/kp/ours.json --out-dir $K/data/frames  # if needed
#    SoccerNet only if G-LEGAL clears it (see licences):
# uv run --project $K python $K/convert.py soccernet /workspace/sn-calib/train --out $K/data/kp/sn_train.json
uv run --project $K python $K/convert.py yolo $K/data/kp/ours.json --images-root $K/data/frames \
    --out $K/data/kp/yolo --val 0.15

# 2) train (base weights yolo11m-pose.pt are fetched by Ultralytics on first use)
uv run --project $K python $K/train.py --set data=$K/data/kp/yolo/data.yaml --set train.project=/workspace/runs

# 3) export + eval + register
uv run --project $K python $K/export_onnx.py --weights /workspace/runs/yolo11m-pose-pv/weights/best.pt \
    --name pitch-kp-yolo11m-pv-v1 --imgsz 960 --check-image <a golden frame>     # --dynamic-batch for TensorRT
uv run python eval/run_homography.py --model $PITCHVISION_MODEL_DIR/pitch-kp-yolo11m-pv-v1.onnx \
    --labels <golden pitch labels> --out eval/out/homography_v1.json      # M1-CV-18 gate: field<1m ≥ 85 %
# → models/registry.yaml entry + model card (merged only if the golden eval passes, AD §13.3)
```

Expected time (estimates — measure on the first run): YOLO11m-pose @960, AMP, batch 16 ≈ 90–120 img/s on a 4090.
- 300 own golden frames (+ ~1.5k with completion-from-H pseudo-dense labels) × 150 epochs ≈ **10–20 min**.
- With SoccerNet-calibration train (~16k imgs, *only if cleared*) × 60 epochs ≈ **2–3 h** (≈ USD 1–2 on RunPod community).
- Export + golden eval ≈ 10 min. Inference: YOLO11m-pose is ~4× lighter than the 266 MB roboflow YOLOv8x-pose
  spike model (300 ms/frame CoreML on M4); `keypoint_every = 5` (AD §6.3) keeps it off the critical path.

## Dataset licences (nothing is downloaded by these scripts)
| Dataset | Status | Notes |
|---|---|---|
| **SoccerNet calibration** (2022/23 challenge, line annotations) | ⚠ **research-only — do not download for product training** | NDA download; terms restrict use to non-commercial research. **G-LEGAL** must clear it (or a commercial agreement with SoccerNet) before weights trained on it run for customers. `convert.py soccernet` exists so a cleared run is one command. Note PLAN M1-CV-19 says "SoccerNet-Calibration + 300 golden": that plan is blocked on this item. |
| **Roboflow `football-field-detection`** (the SP-03 spike model's data / weights) | ⚠ / ✗ | Spike weights `football-pitch-detection.pt` are AGPL (Ultralytics-trained) and were used only for the SP-03 / M1-CV-05 baseline on a dev Mac. Never shipped or registered. Dataset images are broadcast footage of unknown rights; its 32 points use a **120 × 70 m** template (see below). |
| **Our golden / CVAT labels** | ✅ labels ours | Footage is copyrighted broadcast kept for internal R&D; frames never enter git (`training/.gitignore`: `data/`, `runs/`). |
| Base `yolo11m-pose.pt` (COCO-pose) | Enterprise | Covered by the Ultralytics Enterprise licence once signed; COCO images CC BY 4.0 (Flickr terms). |

## Labelling guide (golden frames, CVAT)
SP-03 showed off-the-shelf data does not transfer to our broadcast mix, so the main lever is **our own labels**:
300 main_wide frames spread over all golden matches (≥ 10 per match, stratified by camera position: left box,
left half, centre, right half, right box, plus zoomed-in and fast-pan frames). Extract with
`convert.py frames --sp09 --out-dir <outside repo or training/keypoints/data/>`.

**CVAT project:** one *skeleton* label `pitch` with 32 points named exactly as `cv_core.pitch.KEYPOINT_NAMES`
(`corner_tl`, `l_box_goal_far`, … `circle_right`; also accepted: `0`…`31`, `kp07`). Export "CVAT for images 1.1".

Coordinates: `x` along the touchline from the **left goal line as seen from the main camera**, `y` from the **far
touchline** towards the camera. "far" = top of the image, "near" = bottom, "l_"/"r_" = image left/right half of the
pitch (*not* home/away). If the main camera sits on the other side of the stadium it is still "far = top".

| Points | What to click |
|---|---|
| 0, 5, 24, 29 corners | outer corner where touchline meets goal line (not the corner arc, not the flag base if offset) |
| 1–4, 25–28 | where the penalty-area / goal-area side lines meet the goal line |
| 6, 7, 22, 23 | goal-area front corners · 9, 12, 17, 20 penalty-area front corners |
| 13, 16 | halfway line × far / near touchline |
| 14, 15 | halfway line × centre circle (far / near crossing) |
| 8, 21 penalty spots, 30/31 circle extremes, 10/11/18/19 virtual points | **only if certain**; otherwise skip — `convert.py yolo` fills them from the homography of the other points (`--complete-from-h`, exact under the plane assumption) |

Rules:
1. Click the **intersection of the line centres** (lines are ~12 cm wide), zoom ≥ 200 %.
2. **Visible** = the point is inside the frame: label it, even if a player stands on it (mark *occluded*, v=1) when
   its position is clear from the two lines. If you cannot place it within ~3 px, skip it.
3. **Outside the frame / hidden by a graphic overlay** → do not label (mark *outside*). Never put points on the
   border: out-of-frame points become v=0, which trains the model to give them low confidence (fixes SP-03's
   hallucinated border points).
4. Minimum per frame: **≥ 4 points not all on one line**; aim for ≥ 6 (enables completion from H). A frame showing
   only the halfway line + circle needs 13/16 or 14/15 **and** a point off that line (circle crossing or a touchline
   corner) — if impossible, skip the frame (degenerate for a homography anyway).
5. Non-standard pitches (pitch length ≠ 105 m, visibly): add `"pitch_size": {"<video>": [L, W]}` to the label file
   (venue data) — markings are fixed by Law 1, only the outline moves.
6. Review: `eval/run_homography.py --pred` with GT-fitted H renders overlays (`--render-dir` outside the repo):
   every template line must sit on the painted line.

## Findings that shaped this (SP-03 + M1-CV-05 baseline, internal)
- The roboflow model and its 32 points assume a **120 × 70 m** pitch. Its box / circle points are true Law-1
  distances but touchline / goal-line points sit at 120 × 70 ⇒ a homography fitted to that template on a real
  ~105 × 68 m pitch is internally inconsistent (mixes scales) and SP-03's line metric scored even correct
  detections low. `cv_core.pitch.template` uses the real dimensions (parametric).
- Line alignment (`cv_core.pitch.lines`) is the quality signal; fit residuals on the fitted keypoints are not.
- Baseline of the spike model under the new pipeline (607 SP-09 main_wide frames, no frames committed):
  `eval/results/homography_m1cv05_baseline_rf_spike.json` — 81 % of frames get a homography that passes the
  degeneracy / plausibility checks, but median h_quality 0.19 and 0.5 % ≥ 0.7; visual spot checks agree (the
  centre circle and box points are systematically misplaced). Off-the-shelf weights are not a usable baseline:
  our own labels are the critical path.
