# training — offline model building (not part of the runtime)

A separate uv project (`training/pyproject.toml`, own `training/uv.lock`), **not** a workspace member: torch,
transformers, scikit-learn and onnx stay out of `cv-core`'s runtime deps (the worker image only needs numpy,
onnxruntime, opencv, PyAV). It depends on `cv-core` / `pitchvision-schemas` by path (editable), so training uses
the exact feature / pre-processing code that runs in production.

```bash
uv sync --project training                      # once (~1 GB: torch, transformers)
uv run --project training python training/<script>.py ...
```

| Script | What | Output |
|---|---|---|
| `export_dfine_onnx.py` | D-FINE (HF) → ONNX, parity check vs torch, CPU/CoreML benchmark | `$PITCHVISION_MODEL_DIR/dfine-m-obj2coco-640.onnx` + registry snippet |
| `train_shots.py` | S2b shot classifier (M1-CV-06): features via `cv_core.shots`, leave-one-video-out CV, export `pv-gbm/v1` JSON | `$PITCHVISION_MODEL_DIR/shots-gbm-{det,colour}-v1.json`, `training/out/` (CV predictions, fold models) |
| `dfine/` | D-FINE-M fine-tune prep (M1-CV-04): dataset converters, config, trainer, smoke test, GPU recipe | see `dfine/README.md` |

`PITCHVISION_MODEL_DIR` defaults to `~/.cache/pitchvision/models`. After building a model, update
`models/registry.yaml` (sha256, bytes) and its model card; `cv_core.registry` refuses files whose hash differs.

## Reproduce M1-CV-06 numbers

```bash
export PITCHVISION_GOLDEN_DIR=/path/to/data/golden          # raw/*.mp4 (never committed)
uv run --project training python training/export_dfine_onnx.py
uv run --project training python training/train_shots.py --export-folds                 # ~5 min on M4 (CoreML)
uv run --project training python training/train_shots.py --no-detector --export-folds   # colour-only fallback
uv run python eval/run_shots.py --pred training/out/shot_preds_lovo_det.json --report eval/results/shots_m1cv06_samples_det.json
uv run python eval/run_shots.py --timeline-models training/out/folds_det --jobs 3 \
    --report eval/results/shots_m1cv06_timeline_det.json                                 # full S2b, ~30 min
```

## 4-class shot labels (later)

`train_shots.py --labels labels.json` takes any `pv-shot-samples/v1` file. With labels in
`main_wide | close_up | replay | other` it fits a softmax HGB and the exported model carries those classes
(identity `class_map`) — `cv_core.shots.ShotClassifier` maps model classes onto `ShotType` without code changes.
Sources: our CVAT per-second labels (PLAN 13.4) and, for research only, SoccerNet camera-shot segmentation
(main camera center/left/right → `main_wide`; close-ups of players/coach/referee/public → `close_up`; spider/goal-line/
inside-goal/other cameras → `other`; segments flagged replay → `replay`). Because replays look like normal shots
frame-by-frame, a 4-class per-frame model will mainly separate `close_up` vs `other`; replay detection proper
(logo wipes, slow motion, shot-sequence context) is planned for M2.
