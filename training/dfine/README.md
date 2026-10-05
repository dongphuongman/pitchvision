# D-FINE-M fine-tune — M1-CV-04 (prep; the run needs an NVIDIA GPU)

Goal: D-FINE-M (Apache-2.0, chosen in SP-11 / G-LEGAL 05/10/2026) fine-tuned on broadcast football with 4 classes
`player, goalkeeper, referee, ball` (AD §6.5 ids 0–3), exported to the same ONNX contract as
`dfine-m-obj2coco-640` (`training/export_dfine_onnx.py`), registered in `models/registry.yaml`.

| File | What |
|---|---|
| `coco_convert.py` | Roboflow / CVAT (COCO) / SoccerNet-v3 → one COCO with our 4 classes; `merge`; `split` by video (no near-duplicate frames across train/val) |
| `config.yaml` | Hyper-parameters (base checkpoint, 640 px, AdamW 2e-4 / backbone ×0.1, cosine, warm-up 500, clip 0.1, EMA 0.9999, bf16) |
| `train_dfine.py` | Trainer: HF `DFineForObjectDetection` + plain PyTorch loop; saves `best/` + `last/` (`save_pretrained`, EMA weights) |
| `smoke_test.py` | 2 steps on a synthetic COCO set (CPU, ~1 min) + ONNX export of the 4-class head |

## Why HF transformers + own loop (not the official repo, not `Trainer`)
One stack from training to `save_pretrained` → `export_dfine_onnx.py --repo runs/.../best` → cv_core: identical
pre/post-processing, nothing re-implemented. The loop keeps D-FINE's specifics explicit (backbone LR multiplier,
no-decay groups, grad clip 0.1, EMA). **Switch to the official repo** (github.com/Peterande/D-FINE, Apache-2.0,
`configs/dfine/custom/dfine_hgnetv2_m_custom.yml`) if val mAP@50 on the golden detection labels misses the target:
it adds mosaic / multi-scale / stop-aug epochs, which matter for small players and the ball. Its checkpoints convert
to HF with `transformers`' `convert_d_fine_original_pytorch_checkpoint_to_hf.py`, so export and cv_core stay unchanged.

## Smoke test (Mac, done)
```bash
uv run --project training python training/dfine/smoke_test.py      # SMOKE_DEVICE=cpu (default) | mps
```
2026-10-05, M4, CPU: converters → 2 optimiser steps (finite loss ~104 → 104, val loss 15.7 → 15.0) → EMA →
`save_pretrained` → ONNX export of the 4-class head → cv_core `DFineDetector` labels ⊂ 0..3. ✓
MPS: the backward pass of the D-FINE decoder fails on torch 2.14 (`mat2 must be a matrix`) — train on CUDA (or CPU).

## GPU run recipe (RunPod RTX 4090 24 GB, or the shared 4090 box)
```bash
# pod: PyTorch 2.x CUDA 12.4 template, 50 GB volume at /workspace
git clone <repo> /workspace/pitchvision && cd /workspace/pitchvision
pip install uv && uv sync --project training
export HF_HOME=/workspace/hf PITCHVISION_MODEL_DIR=/workspace/models

# 1) data → training/data/det/ (images + COCO). Only cleared sources (see licences below).
uv run --project training python training/dfine/coco_convert.py roboflow  /workspace/rf/train/_annotations.coco.json \
    --prefix roboflow/train/ --out training/data/det/roboflow.json
uv run --project training python training/dfine/coco_convert.py cvat      /workspace/cvat/instances_default.json \
    --prefix ours/ --out training/data/det/ours.json
uv run --project training python training/dfine/coco_convert.py merge training/data/det/{roboflow,ours}.json \
    --out training/data/det/all.json
uv run --project training python training/dfine/coco_convert.py split training/data/det/all.json --val 0.1 --by video
#   → all_train.json / all_val.json ; images under training/data/det/images/{roboflow,ours}/...

# 2) train (bf16, batch 16 @640)
uv run --project training python training/dfine/train_dfine.py --config training/dfine/config.yaml \
    --set data.train=training/data/det/all_train.json --set data.val=training/data/det/all_val.json \
    --set data.images_root=training/data/det/images --set out_dir=runs/dfine-m-pv-v1

# 3) export + eval + register
uv run --project training python training/export_dfine_onnx.py --repo runs/dfine-m-pv-v1/best \
    --name dfine-m-pv-v1-640 --check-image <a golden frame>            # add --dynamic-batch for TensorRT
uv run python eval/run_detection.py ...                                # golden bbox labels (CVAT, PLAN 13.4)
# → models/registry.yaml entry + models/dfine-m-pv-v1-640.md (PR merged only if golden eval passes, AD §13.3)
```

Expected time (estimates — measure on the first run): D-FINE-M @640, bf16, batch 16 ≈ 60–80 img/s on a 4090.
- Roboflow (~0.5–0.7k imgs) + our 2k CVAT frames ≈ 2.6k imgs × 72 epochs ≈ **40–60 min** (raise `epochs` for small sets).
- With SoccerNet-v3 (~33k imgs, *only if G-LEGAL clears it*) × 36 epochs ≈ **4–6 h** (≈ USD 3–5 on RunPod community 4090).
- Export + golden eval: ~10 min.

## Dataset licences (nothing is downloaded by these scripts)
| Dataset | Status | Notes |
|---|---|---|
| **SoccerNet-v3** (bboxes on action/replay frames) | ⚠ **research-only — do not download for product training** | Download requires signing SoccerNet's NDA; terms restrict use to non-commercial research. PLAN G-LEGAL (deadline 15/01/2027) must clear it first; weights trained on it may be tainted for the SaaS. Converter provided so a cleared run is one command. |
| **Roboflow Universe `football-players-detection`** | ⚠ verify | Universe shows the licence per dataset (this one is listed as CC BY 4.0 — re-check the page, keep attribution). Its images are frames of DFL Bundesliga broadcasts (from the DFL Kaggle data): the *labels* may be CC BY, the *footage* is DFL's — add to G-LEGAL. |
| **DFL Bundesliga Data Shootout** (Kaggle) | ⚠ competition rules | Videos + event labels, **no boxes**. Kaggle competition data is typically licensed for the competition / non-commercial use — not usable for product weights unless DFL/legal agrees. At most: unlabelled frames for pseudo-label + CVAT review. |
| **Our golden / CVAT labels** | ✅ labels ours | Footage is copyrighted broadcast kept for internal R&D (see `.gitignore`); frames never leave our storage. |
| Base weights `dfine-medium-obj2coco` | ✅ Apache-2.0 | Objects365 pre-training data licence still open (G-LEGAL). |
