# Model card — `siglip-base-p16-224` (shirt embedder for the team split)

| | |
|---|---|
| What | SigLIP-base/16 **vision tower** at 224×224, `pooler_output` (768-d attention-pooled embedding), exported to ONNX |
| Source | `google/siglip-base-patch16-224` (HF transformers) |
| Licence | Apache-2.0 (code + weights) |
| Used by | S3 calibrate (`cv_core.teams.fit_team_model`, M1-CV-09) and S4 per-track team vote (`cv_core.teams.TrackTeamVoter`, M1-CV-10) |
| Build | `uv run --project training python training/export_siglip_onnx.py` (legacy TorchScript exporter, opset 17, dynamic batch; the patch Conv gets explicit pads for CoreML) |
| Runtime | `cv_core.teams.SiglipEmbedder(Runtime.select(...).model(path))` — pre-processing in cv_core (cv2 + numpy), output L2-normalised |

## Contract
- input `pixel_values` float32 `[N,3,224,224]`, RGB, bicubic resize to 224×224 (no crop), `(x/255 − 0.5)/0.5`;
- output `pooler_output` float32 `[N,768]`.

## Verification (2026-10-06, M4)
- Graph parity, same `pixel_values` through torch and ONNX: min cosine 1.000000, max |Δ| 1.3e-5.
- End-to-end (cv2 bicubic vs HF PIL bicubic on 12–64 px crops): min cosine 0.990, mean 0.996.
- Speed: ORT CPU EP 40 ms/crop (batch 32); ORT CoreML EP (MLProgram, static batch) 18 ms/crop but the MLProgram compile
  takes ~3 min per process, so the worker uses the CPU EP for this model on a Mac (`PV_EMBEDDER_PROVIDER` overrides).
  On NVIDIA (ORT CUDA / TensorRT FP16) it is expected to be ~1–2 ms/crop.

## Why this model
SP-04: SigLIP + UMAP + KMeans(2) on torso crops split the teams ~98 % correctly (visual check, 19 golden matches);
HSV colour histograms were clearly worse. M1-CV-09 keeps SigLIP and replaces UMAP by a numpy spectral split + whitened-LDA predictor (see
`eval/results/teams_m1cv09_vs_umap.json`).

## Known limits
- No fine-tuning on football kits; similar kits (e.g. white vs light grey) and goalkeepers / referees rely on the
  outlier radius of the team model. On golden matches referees are mostly absorbed into the nearer team, while the
  -1 bucket (5–10 % of crops) is dominated by sharp close-to-camera crops: role classification needs the fine-tuned
  4-class detector (M1-CV-04), not the embedding radius.
- 370 MB fp32; an fp16 / int8 variant is a later optimisation once a GPU parity check exists.
