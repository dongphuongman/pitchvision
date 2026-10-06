# Pipeline stages contract (M1 W3: M1-BE-09…12, M1-CV-08…10)

Source of truth between the **orchestrator / task layer** (`workers/pitchvision_worker/{orchestrator,compute,tasks.py}`)
and the **stage functions** (`workers/pitchvision_worker/stages/*.py`, built on `cv_core`). Change both in one PR.

## Rules
- A stage function is pure w.r.t. infrastructure: it takes **local paths + `PipelineConfig`**, writes **local files**,
  returns a small result object. No S3, Redis, DB or Celery inside stages.
- The task layer downloads inputs from the object store into a temp dir, calls the stage, uploads outputs under the
  keys below, writes `_SUCCESS` **last** (checkpoint: a retried task that sees `_SUCCESS` returns immediately),
  updates `job_tasks`, and publishes progress.
- Object keys come only from `pitchvision_schemas.keys` (storage class first).

## Stages and job states (AD §6, §16.1)
| Job status | Stage | Function (stages/) | Runs on | Inputs → outputs |
|---|---|---|---|---|
| ingesting | S1 ingest (YouTube) / wait for upload `ready` | `ingest.*` (exists) | queue `ingest` | URL → `keys.source_key` |
| calibrating | S2 normalise | `normalize.run_normalize(original, out_dir, config) -> NormalizeOutput` (exists) | cpu | source → `keys.normalized_key`, probe fields into `videos` |
| calibrating | S2b shots | `shots.run_shots(normalized, config) -> ShotTimeline` (exists) | gpu (light) | normalized → table `shots` + `keys.shots_key` (`media/…/shots.json`) |
| calibrating | segment plan | `segments.plan_segments(timeline, duration_ms, config) -> list[Segment]` | cpu (in orchestrator) | ShotTimeline → segments (main_wide only, cut on shot boundaries, merged up to `segment_target_s`) |
| calibrating | S3 calibrate | `calibrate.run_calibrate(normalized, timeline, out_dir, config) -> CalibrateOutput` | gpu | → `results/…/jobs/{job}/models/team_model.json` (+ sample crops grid) |
| inferring | S4 infer (fan-out per segment) | `infer.run_infer(normalized, segment, team_model, out_dir, config) -> InferOutput` | gpu | `GpuTask.segment` + inputs `normalized`, `team_model` → `keys.segment_prefix(...)/detections.parquet` + `_SUCCESS` |
| postprocessing | S5 stitch, S7 tiles, S8 load-db | stubs in W3 (W4 tasks M1-CV-13, M1-BE-13/14) | cpu | — |
| done | — | — | — | — |

`Segment` lives in `pitchvision_schemas.pipeline` (index, t0_ms, t1_ms; half-open, on the 25 fps normalized timeline).

## detections.parquet (S4 v1, per segment)
`frame_idx int32` (normalized timeline) · `ts_ms int32` · `track_id int32` (segment-local, ByteTrack) · `cls int8`
(0 player · 1 goalkeeper · 2 referee · 3 ball — v1 from a COCO detector: person→0, sports ball→3) · `team int8` (0/1, -1 none)
· `x1,y1,x2,y2 int16` (pixels on normalized video) · `conf float16` · `on_pitch bool` · `px,py float32` (NaN until
homography lands in W4) · ZSTD. Schema + writer live in `cv_core.io.parquet` with a version field in file metadata.

## GPU dispatch (AD §7)
- Orchestrator never calls a GPU stage directly: it builds a `GpuTask` (stage ∈ shots/calibrate/infer, inputs = object
  keys, output_prefix, config, image_tag; for `infer` also `segment: Segment` — the plan lives in the `plan` job_task
  payload, there is no `segments.json` object) and calls `ComputeBackend.submit`. S2 normalise is a `CpuTask`
  (stage `normalize`, `outputs = {"normalized": keys.normalized_key}`) on the local `cpu` queue. Both models live in
  `pitchvision_schemas.compute`.
- `LocalGpuBackend` sends Celery task `gpu.run_stage` (payload = `GpuTask.model_dump()`) to queue `PV_LOCAL_GPU_QUEUE`
  (default `gpu`; set `cpu` on a Mac / CPU-only compose so the same pipeline runs end-to-end slowly).
- `RunPodBackend` is W5 (M1-BE / RunPod); keep the interface ready.

## Progress & events
- Redis pub/sub channel `job:{job_id}`; messages are `pitchvision_schemas.ws` `ProgressEvent` / `CompletedEvent` /
  `FailedEvent` / `CancelledEvent` JSON. The API WebSocket fans them out (topic `job:{id}`); `cancelled` is published
  by `orchestrate.cancel_job` (API: POST /jobs/{id}/cancel and DELETE /videos/{id}).
- The socket's own control frames (`subscribed`, `unsubscribed`, `ping`, `pong`, `error`) are part of the same
  `ServerEvent` union (exported JSON Schema `ws_server_event`).
- Per-segment retries: 3 attempts, backoff 30 s / 2 min / 5 min; other segments continue (AD §16).

## What the API reads back (VideoOut.latest_job)
`GET /api/v1/videos[/{id}]` embeds the newest job: `{id, status, progress, stage, error, coverage, pitch_calibration}`.
`coverage` is `jobs.coverage`; `pitch_calibration` (`ok` | `degraded` | `manual`, the manifest's `pitch.status`) is
read from **`jobs.detail.pitch_calibration`** — null until the orchestrator writes it once S3/S5 have calibrated the
pitch (W4, M1-CV-11/12). The web shows "Ready · Low pitch quality" for `degraded`.

## Video deletion (AD §14)
`DELETE /api/v1/videos/{id}` cancels the active job (→ `orchestrate.cancel_job`), soft-deletes the row and enqueues
`cpu.delete_video_objects(org_id, video_id)` (`pitchvision_worker.erasure`): every object under
`keys.video_prefixes` + in-progress multipart uploads, with a final pass 4 h later for outputs of stage tasks that
were mid-flight. Stages must therefore only write under the keys above.
