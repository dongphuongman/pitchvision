# Pipeline stages contract (M1 W3: M1-BE-09…12, M1-CV-08…10; W4: M1-CV-11…13, M1-BE-13…16)

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
| inferring | pitch (OPTIONAL, fan-out per segment, task key `pitch:NNN`) | `pitch.run_pitch(normalized, segment, out_dir, config, *, cuts_ms) -> PitchOutput` | gpu | `GpuTask(stage="pitch").segment` + inputs `normalized`, `shots` → `keys.segment_prefix(...)` + `pitch/homography.parquet` + `pitch/_SUCCESS`; result `status` `ok` / `unavailable` |
| postprocessing | S5 stitch | `stitch.run_stitch(segments: list[SegmentInput], timeline, out_dir, config) -> StitchOutput` | cpu (`CpuTask` stage `stitch`) | inputs `shots`, `detections_NNN`, `homography_NNN` (only segments whose pitch task is done with `ok`), `params.segments` = plan → `results/…/jobs/{job}/tracks/tracks.parquet` + `_SUCCESS`; result `pitch` = {status, homography_ok_pct, …} |
| postprocessing | S7 tiles | `tiles.run_tiles(tracks, timeline, out_dir, config, *, duration_ms, frame_count) -> TilesOutput` | cpu (`tiles`) | inputs `tracks`, `shots`; `params` org/video/job/revision/duration_ms/frame_count → `keys.tile_key(org, video, job, revision, n)` for every 10 s of the video; `_SUCCESS` under `results/…/stages/tiles/` |
| postprocessing | S8 load-db | `load_db.prepare_load(tracks, team_model, config) -> LoadBatch` + task-layer writer `results_db.write_results` | cpu (`load_db`) | inputs `tracks`, `team_model`; `params` org/video/job/revision → tables `positions` (5 Hz), `teams`, `players`; `_SUCCESS` under `results/…/stages/load_db/` |
| postprocessing | manifest | `orchestrator.manifest.build_manifest(...)` | orchestrator (inline, LAST) | normalize/stitch/tiles results + shots + team model → `keys.manifest_key` (`media/…/manifest.json`), job_task `manifest` |
| done | — | — | — | — |

Order inside `postprocessing`: `stitch` → `tiles` + `load_db` (dispatched together) → `manifest` → job `done` (same
step). Every post stage has its `job_tasks` row (`stitch`, `tiles`, `load_db`, `manifest`) and progress `stage`; a
failed `stitch` / `tiles` / `load_db` fails the job (retry resumes from it; finished stages are skipped through
`_SUCCESS` / done rows). `CpuTask.params` carries non-key arguments (ids, revision, duration, segment plan).

`Segment` lives in `pitchvision_schemas.pipeline` (index, t0_ms, t1_ms; half-open, on the 25 fps normalized timeline).

## detections.parquet (S4 v1, per segment)
`frame_idx int32` (normalized timeline) · `ts_ms int32` · `track_id int32` (segment-local, ByteTrack) · `cls int8`
(0 player · 1 goalkeeper · 2 referee · 3 ball — v1 from a COCO detector: person→0, sports ball→3) · `team int8` (0/1, -1 none)
· `x1,y1,x2,y2 int16` (pixels on normalized video) · `conf float16` · `on_pitch bool` · `px,py float32` (always NaN in
S4: the optional pitch stage writes homography.parquet and S5 projects) · ZSTD. Schema + writer live in `cv_core.io.parquet` with a version field in file metadata.
Player track ids start at 1; the ball is untracked in v1 (`track_id = -1`, best detection per frame). Rows exist only
for tracked players (+ ball), each with its detector box/score; `on_pitch = false` marks a box that continues a track
outside the grass mask (only on-pitch boxes may start a track).

## homography.parquet (pitch stage, per segment, OPTIONAL)
`frame_idx int32` (every frame of the segment) · `h0…h8 float64` (row-major image→pitch H in metres on the 105×68
template; NaN = the smoother has no reliable estimate) · `h_quality float32` (smoothed line-alignment quality, never the
keypoint fit residual — SP-03). Keypoints run every `keypoint_every_n` frames; `HomographySmoother` is reset at every
shot cut inside the segment. The stage runs only when `models/registry.yaml` has a `pitch_keypoints` entry with local
weights (sha256-checked); otherwise it returns `status = "unavailable"` and writes nothing. The orchestrator only
dispatches `pitch:NNN` tasks when the image's registry lists that entry (decided once at plan time, plan payload
`pitch`). **M1 ships no keypoint model**: the SP-03 roboflow model is AGPL and must never be registered (G-LEGAL);
M1-CV-05 delivers the licensed one. A failed pitch task never fails the job.

## tracks.parquet (S5 v1, one per job — AD §6.5)
detections columns with GLOBAL `track_id` (players from 1, ball `-1`), no `on_pitch`, plus `h_quality float16` (NaN =
no H for the frame) and `interpolated bool`; sorted by (frame_idx, track_id); `cv_core.io.parquet.{write,read}_tracks`.
Rules (`cv_core.track.stitch`): tracks are linked across a segment boundary ONLY inside one shot (boundary not a cut,
segments contiguous) — last/first box IoU ≥ 0.2, same role, not of different teams, gap ≤ 1 s, Hungarian; nothing is
linked across shot cuts in M1 (ReID = M2). Holes ≤ 0.4 s inside a track are linearly interpolated (`interpolated`);
team/class of a global track = majority. With homography: `px, py` = box bottom-centre projected to metres (NaN without
H / > 15 m off the pitch). Pitch verdict (`stitch` result `pitch.status`, → `jobs.detail.pitch_calibration` and the
manifest): `unavailable` (no homography at all) · `degraded` (> 30 % of main_wide frames with `h_quality` < 0.5,
frames without H count as below) · `ok`. `homography_ok_pct` = % of main_wide frames with `h_quality` ≥ 0.5.

## Tiles (S7) and their signing
One gzip JSON per 10 s (`pitchvision_schemas.Tile`, validated before writing) for the WHOLE duration, every frame
present: non-analysed shots have `objs = []`, `hq = null` and their `[f0, f1_excl, type]` run in `shots`. Objects
`[track_id, cls, team, x1, y1, x2, y2, conf, px, py]` (px/py null without H). Deterministic bytes (gzip mtime 0) —
tile keys are write-once per revision (`keys.tile_prefix(org, video, job, revision)`), cached as immutable.
The bucket is never public. `GET /api/v1/results/{video}/manifest` returns `tile_url_template` =
`/api/v1/tiles/{video}/{job}/r{revision}/{tile_no}.json.gz?exp={unix}&sig={sig}` with
`sig = base64url(HMAC-SHA256(PV_TILE_SIGNING_KEY, "tiles/{video}/{job}/r{revision}/\n{exp}"))`, TTL
`PV_TILE_URL_TTL_S` (6 h). The tile route checks the signature FIRST (403 bad / expired / tampered, `no-store`), then
streams the object with `Content-Encoding: gzip` + `Cache-Control: public, max-age=31536000, immutable`. The stored
`manifest.json` holds the object-key template, never a signature. The Cloudflare Worker (cloud) will verify the same
HMAC later; `PV_TILE_BASE_URL` points manifests at it.

## S8 load-db and the `pv_worker` role
`positions` (5 Hz: frames with `frame_idx % (fps/5) == 0`, players + ball, `vx, vy` m/s from the previous 5 Hz sample,
NULL above 12 m/s for people, `q` = h_quality), `teams` (cluster 0/1, "Team A/B", colour = S3 crop-mean
`team_model.json stats.colors`), `players` (one per global track, role from the class). Idempotent per (video,
revision): positions of the video are replaced, teams upserted by cluster, players by (video, track_id), stale
unconfirmed players removed; `confirmed` rows are never touched. Workers connect as **`pv_worker`** (migration 0004):
member of `pv_app` (same RLS-bound grants) + `INSERT` on the positions hypertable (which has no RLS — Timescale
compression); the `positions_org_guard` trigger rejects rows whose `org_id` ≠ `app.org_id`, deletes go only through
`pv_delete_positions(video)` (SECURITY DEFINER, scoped to `app.org_id`). The API (`pv_app`) still reads only
`positions_org`. Compose: `PV_WORKER_DB_PASSWORD` (migrate enables the login).

## Manifest (written LAST)
`keys.manifest_key` (per video) is written by the orchestrator after tiles + load-db, in the same step that marks the
job `done`, so a manifest never lists missing tiles. The API serves it for the latest `done` job (409 `processing`
while the first job runs or when the stored manifest belongs to a newer job; 404 when nothing finished) and overrides
`teams` with the `teams` table (user edits win).

## Stage outputs / errors (M1-CV-08…10)
- `plan_segments` returns `[]` when there is no analysable footage → job error `NO_WIDE_SHOTS`.
- `run_calibrate` writes `team_model.json` (`pv-team-model/v1`, portable JSON — replaces AD's `team_model.pkl`) and
  `team_samples.jpg`; raises `calibrate.NoWideShots` (`code = NO_WIDE_SHOTS`) or `cv_core.teams.TeamSeparationFailed`
  (`code = TEAM_SEPARATION_FAILED`, `.reason` ∈ too_few_crops / imbalanced / not_separable) — both permanent.
- `run_infer` returns `InferOutput(paths={"detections": …}, frames, fps_measured, stats)`; models resolve through
  `models/registry.yaml` (`dfine-m-obj2coco-640`, `siglip-base-p16-224`); `PV_RUNTIME_PROVIDER` picks the provider.

## GPU dispatch (AD §7)
- Orchestrator never calls a GPU stage directly: it builds a `GpuTask` (stage ∈ shots/calibrate/infer/pitch, inputs =
  object keys, output_prefix, config, image_tag; for `infer` / `pitch` also `segment: Segment` — the plan lives in the
  `plan` job_task payload, there is no `segments.json` object) and calls `ComputeBackend.submit`. S2 normalise and
  S5/S7/S8 are `CpuTask`s (stages `normalize`, `stitch`, `tiles`, `load_db`; `outputs` = exact keys outside the
  prefix, `params` = non-key arguments) on the local `cpu` queue. Both models live in `pitchvision_schemas.compute`.
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
`coverage` is `jobs.coverage`; `pitch_calibration` (`ok` | `degraded` | `manual` | `unavailable`, the manifest's
`pitch.status`) is read from **`jobs.detail.pitch_calibration`** — null until S5 has run; the orchestrator writes it
from the `stitch` result (W4). The web shows "Ready · Low pitch quality" for `degraded`; `unavailable` (no keypoint
model in M1) means no pitch coordinates / tactical map.

Results API (M1-BE-15/16): `GET /api/v1/results/{video}/manifest`, `/teams`, `/tracks?from_ms&to_ms&limit&cursor`
(5 Hz rows from `positions_org`, window ≤ 10 min, keyset cursor) and the signed tile route above.

## Video deletion (AD §14)
`DELETE /api/v1/videos/{id}` cancels the active job (→ `orchestrate.cancel_job`), soft-deletes the row and enqueues
`cpu.delete_video_objects(org_id, video_id)` (`pitchvision_worker.erasure`): every object under
`keys.video_prefixes` + in-progress multipart uploads, with a final pass 4 h later for outputs of stage tasks that
were mid-flight. Stages must therefore only write under the keys above.
