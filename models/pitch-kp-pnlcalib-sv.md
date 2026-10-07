# pitch_keypoints — PnLCalib single-view keypoint HRNet (RESEARCH-ONLY, internal demo)

**Not for customers.** Weights trained on SoccerNet-Calibration (research-only data); upstream code GPL-2.0
(github.com/mguti97/PnLCalib, release v1.0.0 `SV_kp`). PO decision 07/10/2026: use it to demo the tactical map on
developer machines until the licensed YOLOv11-pose fine-tune (M1-CV-05, Ultralytics Enterprise) exists.

Guard: `pitchvision_worker.stages.pitch.research_models_allowed` — loaded only with `PV_ALLOW_RESEARCH_MODELS=1`
**and** `PV_ENV` local/test. Staging / production refuse it (the orchestrator then plans no pitch stage).

| | |
|---|---|
| Graph | `image [1,3,540,960]` RGB /255 stretched → `heatmaps [1,57,270,480]` (background channel dropped) |
| Decode | `cv_core.pitch.HeatmapPitchModel` (our code): per-channel peak, threshold 0.3434 → conf 0.5, world table in the registry (`keypoints_world`, 4 crossbar points null) → `estimate_homography` (MAGSAC + refine) → line-alignment `h_quality` |
| Not used | PnLCalib's camera calibration / PnL refinement and line model (GPL code; keypoints alone are enough here) |
| Build | `spikes/pnlcalib/` (own venv, upstream repo + weights git-ignored): `export_onnx.py`, parity vs torch 3.5e-9 |
| Eval | highlight ESP–CZE, 33 main_wide shot midpoints: 28 with `h_quality ≥ 0.5`, median 0.82 (keypoints-only; full PnLCalib 0.87). Visual check of overlays OK. Not run on the golden set. |
| Speed | 0.3 s/frame MPS (torch), 0.9 s/frame onnxruntime CPU 4 threads (M4); ~2.3 GB RSS per worker process → `PV_CPU_CONCURRENCY=1` on an 8 GB Docker VM |
