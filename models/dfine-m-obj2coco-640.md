# Model card — `dfine-m-obj2coco-640` (detector, pre-fine-tune)

| | |
|---|---|
| What | D-FINE-M (HGNetV2-B2 backbone, 300 queries), COCO-80 head, exported to ONNX at a fixed 640×640 input |
| Source | `ustc-community/dfine-medium-obj2coco` (HF transformers port of Peterande/D-FINE) — Objects365 pre-train → COCO fine-tune |
| Licence | Apache-2.0 (code + weights). **Open:** Objects365 data licence for the obj2coco pre-training (PLAN G-LEGAL) |
| Used by | `shots-gbm-det` person features (M1-CV-06); base for the M1-CV-04 fine-tune (`training/dfine/`) |
| Build | `uv run --project training python training/export_dfine_onnx.py` (legacy TorchScript exporter, opset 17, static batch 1) |
| Runtime | `cv_core.detect.DFineDetector(Runtime.select(...).model(path))` — pre/post-processing in cv_core (numpy) |

## Contract
- input `pixel_values` float32 `[1,3,640,640]`, RGB, stretched (no letterbox), `/255`, no mean/std;
- outputs `logits [1,300,80]` (sigmoid, focal-loss head), `pred_boxes [1,300,4]` cxcywh in 0..1;
- post-process (`cv_core.detect.dfine.postprocess`): sigmoid → top-300 over queries×classes → xyxy px → `conf`.
  Mirrors `RTDetrImageProcessor.post_process_object_detection`.

## Verification (2026-10-05, M4)
- Graph parity, same `pixel_values` through torch and ONNX: 18/18 detections ≥ 0.35 matched, IoU 1.000, Δscore 0.0000.
- End-to-end (cv2 INTER_AREA resize vs HF PIL bilinear): 18/18 matched, mean IoU 0.997, max Δscore 0.016.
- Speed, batch 1 @640: ORT CPU EP 99 ms (10 fps); ORT CoreML EP (MLProgram) 42 ms (24 fps).
  CoreML needs the **static-batch** export and `ModelFormat=MLProgram` (the dynamic-batch graph aborts in MPSGraph;
  NeuralNetwork format fails). `--dynamic-batch` exports a batched graph for CUDA/TensorRT.

## Known limits
- COCO classes only (person / sports ball); no player/goalkeeper/referee split until the M1-CV-04 fine-tune.
- SP-11: recall on persons 98 %, ~15–25 % false balls at conf 0.35 on broadcast — do not use its ball output as-is.
