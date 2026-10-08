# Pipeline stages contract (M1 W3: M1-BE-09…12, M1-CV-08…10; W4: M1-CV-11…13, M1-BE-13…16; SCR-14/15: M1-BE-20)

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
| calibrating | S3 calibrate | `calibrate.run_calibrate(normalized, timeline, out_dir, config, *, seeds=None) -> CalibrateOutput` | gpu | → `results/…/jobs/{job}/models/team_model.json` + `team_samples.jpg` (crop grid) + `team_candidates.json`; optional input `seeds` (job kind `team_rerun`) |
| inferring | S4 infer (fan-out per segment) | `infer.run_infer(normalized, segment, team_model, out_dir, config) -> InferOutput` | gpu | `GpuTask.segment` + inputs `normalized`, `team_model` → `keys.segment_prefix(...)/detections.parquet` + `_SUCCESS` |
| inferring | pitch (OPTIONAL, fan-out per segment, task key `pitch:NNN`) | `pitch.run_pitch(normalized, segment, out_dir, config, *, cuts_ms) -> PitchOutput` | gpu | `GpuTask(stage="pitch").segment` + inputs `normalized`, `shots` → `keys.segment_prefix(...)` + `pitch/homography.parquet` + `pitch/_SUCCESS`; result `status` `ok` / `unavailable` |
| postprocessing | S5 stitch | `stitch.run_stitch(segments: list[SegmentInput], timeline, out_dir, config) -> StitchOutput` | cpu (`CpuTask` stage `stitch`) | inputs `shots`, `detections_NNN`, `homography_NNN` (only segments whose pitch task is done with `ok`), `params.segments` = plan → `results/…/jobs/{job}/tracks/tracks.parquet` + `_SUCCESS`; result `pitch` = {status, homography_ok_pct, …} |
| postprocessing | recompute (job kind `recalibrate` only, replaces S5) | `recompute.run_recompute(tracks, timeline, out_dir, config, *, points, frame_ms, scope, normalized) -> RecomputeOutput` | cpu (`recompute`) | inputs `tracks` (the PARENT job's tracks.parquet), `shots`, `normalized` (camera tracking over the calibrated shot); `params.calibration` = {points, frame_ms, scope} → this job's `results/…/tracks/tracks.parquet` + `_SUCCESS`; result `pitch` = {status: `manual`, homography_ok_pct} |
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
keypoint fit residual — SP-03). Keypoints run every `keypoint_every_n` frames and the camera is tracked on EVERY frame in between
(`cv_core.pitch.motion`: LK optical flow + RANSAC → frame-to-frame H, chained forwards / backwards from the smoothed
keyframes and blended; highlight ESP–CZE @ 1 keyframe/s: mean h_quality 0.812 vs 0.778 interpolated, usable frames
89.2 % vs 86.9 %); `HomographySmoother` is reset at every
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
- Calibrate also writes `team_candidates.json` = `{"fps", "frames": [{"frame_idx", "t_ms", "boxes": [[x1, y1, x2,
  y2], …]}]}` (≤ 20 sample frames, player candidates — SCR-14 manual mode) and `team_model.json` `stats.grid`
  (`tile_w/tile_h/label_w/per_row/width/height`, `rows: [{label, cluster, count}]`: where each crop sits in
  `team_samples.jpg`). On `TEAM_SEPARATION_FAILED` both files go to `out_dir/diagnostics/`; the executor uploads
  `diagnostics/` of a FAILING stage (never `_SUCCESS`) → `…/models/diagnostics/`.
- Seeded calibrate (`seeds = {"0": [{"t_ms", "box"}, …], "1": […]}`, ≥ 2 per team): seed torso crops are embedded
  and `cv_core.teams.fit_seeded_team_model` starts from the seed centroids — cluster j IS the user's team j, no
  separability gate, `stats.seeded`, `stats.seed_agreement`. Without seeds the default path is unchanged.
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

## Derived jobs: SCR-14 manual teams, SCR-15 manual calibration (M1-BE-20)
`jobs.detail.kind` (`pitchvision_schemas.JobKind`; absent = `analyze`) + `detail.parent_job_id`, created by the API as
ordinary jobs (same one-active-job rule, `orchestrate.start_job`, WS topic `job:{id}`, `JobOut.kind/parent_job_id`).
On its first step the orchestrator INHERITS the parent's finished stages: their `job_tasks` rows are copied as `done`
(`backend = "inherited"`, `external_id` = the job that produced the objects) and `detail.inherit = {task_key:
job_id}` makes `_prefix(key)` read that job's objects (chains resolve to the real producer). `detail` keys `kind,
parent_job_id, inherit, team_seeds, calibration, correction_id, apply_after, correction_ids` survive progress updates.

| kind | inherits | runs | API |
|---|---|---|---|
| `team_rerun` | normalize, shots, plan (when done in the parent — e.g. the job that failed `TEAM_SEPARATION_FAILED`) | calibrate with input `seeds` (`detail.team_seeds` → `results/…/jobs/{job}/inputs/team_seeds.json`), then infer / pitch / stitch / tiles / load_db / manifest as usual (GPU) | `POST /results/{v}/teams/manual` |
| `recalibrate` | normalize, shots, plan, calibrate (parent must be `done`) | `recompute` (CPU) → tiles → load_db → manifest; no GPU stage | `POST /results/{v}/calibration` |
| `repitch` | normalize, shots, plan, calibrate + every `infer:NNN` (parent must be `done`) | `pitch:NNN` → stitch → tiles → load_db → manifest; fails `HOMOGRAPHY_UNAVAILABLE` when no pitch model is usable here | `POST /results/{v}/repitch` |
| `edit` | normalize, shots, plan, calibrate (parent must be `done`) | waits `detail.apply_after` → `edit_tracks` (CPU) → tiles → load_db → manifest; no GPU, no notification | `POST /results/{v}/corrections` (debounced) |

**Revisions.** `POST /calibration` bumps `videos.revision` (AD §8.4) in the same transaction as its `corrections` row
and the job; the recalibrate job's tiles go to `keys.tile_key(org, video, NEW job, revision, n)` and the new manifest
carries that revision. Tile keys stay write-once. Until the new manifest is written (last step, job → done) the API
keeps serving the previous `done` job's manifest + tiles; a failed / cancelled recalibration leaves them live.
The next `POST /calibration` takes the newest **done** job as parent, so after a recalibrate that failed
`HOMOGRAPHY_UNAVAILABLE` (SCR-13 E11 → SCR-15) the user simply calibrates again; `409 no_analysis` only when the
video has never had a finished analysis (an `analyze` job never fails on the pitch: S5 reports `unavailable`).
Team renames / colours / swaps (`PATCH /teams`) never touch tiles: tiles and positions carry the CLUSTER (`team`
0 / 1), the manifest's `teams` (from the `teams` table) maps cluster → name / colour, so **a swap exchanges name +
colour between the cluster-0 and cluster-1 rows** and the player re-colours on the next manifest fetch. No revision
bump; the change is recorded as a `corrections` row (`type = "teams"`). `team_samples` / `calibration` rows record the
other two actions (with `job_id`).

**Track corrections (`edit`, M2-BE-01/02, SCR-22).** `POST /results/{v}/corrections` records `merge {from, into}`,
`split {track, at_ms}` (the API fixes `new_track` = max known id + 1) or `remove {track, from_ms?, to_ms?}` (no range =
"Not a player") as a `corrections` row, bumps `videos.revision` and queues ONE `edit` job per burst:
`detail.apply_after` = now + 30 s, pushed back by every new correction / undo until the orchestrator dispatches
`edit_tracks` (it then writes `detail.correction_ids` — the API's `pending` = live ids ≠ the latest done edit's).
`DELETE /corrections/{id}` (Undo) sets `payload.undone`. `edit_tracks` (`pitchvision_worker.stages.edit_tracks`)
re-applies the WHOLE live list, in order, to the tracks of the nearest ancestor that is not an `edit` (so an undone
correction disappears; every operation is idempotent); output at `results/…/jobs/{job}/tracks/tracks.parquet`
like S5, pitch verdict = that ancestor's. `recompute` also reads the unedited ancestor's tracks, so a recalibration
or repitch after edits leaves the corrections `pending` (no edit job yet): the client calls
`POST /corrections/apply` once that job is done (also used when a correction arrived while another job ran).
Team / role fixes stay display-level (`PATCH /players`). Corrections are keyed by the analysis' track ids: a new
`analyze` / `team_rerun` re-tracks, so old corrections no longer match (to clear on re-analysis — not done yet).
Compose ESP–CZE: 3 corrections in one burst → applied 52 s after the first (30 s debounce + ~20 s CPU).

**Manual H (recompute).** `cv_core.pitch.fit_homography` on the ≥ 4 (≤ 8) image ↔ template pairs (all clicked on
one frame `frame_ms`); refused with `HOMOGRAPHY_UNAVAILABLE` (permanent) when degenerate or RMSE > 5 m. Scope `shot`:
every frame of the analysed shot containing `frame_ms` gets the manual H (overrides automatic H there), **moved with
the camera**: recompute decodes that shot of `normalized.mp4` (input `normalized`), `cv_core.pitch.motion.CameraMotion`
estimates frame-to-frame motion and `propagate` chains the clicked H forwards / backwards. Line re-anchoring
(M1-CV-20): on the clicked frame and every 25th frame the chained H is refined onto the painted lines
(`cv_core.pitch.refine.refine_homography`); accepted refinements with line alignment ≥ 0.5 become extra keyframes
(quality = their alignment; the clicked frame keeps the click quality) and the chain is recomputed — this also
corrects imprecise clicks. `h_quality` = `1 − RMSE/2 m`
(clipped) on the clicked frame, minus 0.002 per chained frame; frames the chain can't reach keep the fixed H with
`h_quality` ≤ 0.3. If the video can't be decoded the whole shot gets the fixed H (warning, job still succeeds).
Scope `video`: plus every analysed frame of other shots whose automatic H is missing or `h_quality` < 0.5 — those get
the fixed H (nothing to track from there). Projection = S5's (`project_rows`: bottom-centre, ≤ 15 m off the pitch).
Verdict `manual`; `homography_ok_pct` recomputed from the per-frame qualities. Result `stats.camera_tracked_frames`,
`stats.line_anchors`. **Limitation:** where no markings are visible (a tight shot of the middle third) anchors are
rejected and the chain alone carries the H; scope `video` only fits shots framed like the calibrated one.

**Line refinement (M1-CV-20).** `cv_core.pitch.refine.refine_homography(mask, H_pitch→image)`: template markings
sampled every 0.5 m, projected, kept on visible grass; damped Gauss-Newton on the truncated, smoothed distance
transform of the white-line mask (point-to-line), coarse-to-fine τ 24 → 3 px @1280, markings weighted √n, markings
with < 10 observable samples dropped. Accepted only if `line_alignment` quality rises by ≥ 0.005; otherwise the input
comes back. Used by the pitch stage on every keypoint estimate (`stats.estimates_refined`) and by recompute (above).
Measured: synthetic perturbations 7–36 px → 0.8–2.3 px; 15 real golden frames (PnLCalib keypoints) mean h_quality
0.666 → 0.738, ~50 ms per frame incl. the line mask.

**Stats (S9, M3 — early).** Computed inside the S8 `load_db` task (`pitchvision_worker.stages.stats.run_stats`, same
tracks + revision) and written to `stats_player` (one row per `players` row = per track until ReID; metrics JSON
`pv-stats/v1`: `observed_s`, `distance_m`, `zones_m` {walk < 7.2, jog, run, sprint ≥ 25.2 km/h}, `top_speed_kmh`
+ `top_speed_ms`, `sprints` (≥ 25.2 km/h for ≥ 1 s), `avg_x/avg_y`, `cov`). Observed = people (no ball) with
finite px/py and `h_quality ≥ 0.5`, runs ≥ 1 s; Gaussian smoothing σ 0.3 s (odd-reflection padding), steps
> 10.5 m/s dropped. The API (`GET /results/{v}/stats`) sums tracks into teams with the CURRENT `players` team /
role (referees and teamless tracks excluded), so SCR-14 fixes count without a re-run; `estimated_distance_m` =
m per observed player-minute × 11 × video minutes, labelled "Estimated" in the UI. `GET /results/{v}/heatmap`
bins the 5 Hz `positions_org` (q ≥ 0.5, no ball / referees) on 2.5 m cells, by team / track / time. `stats_team`
stays unused (teams are derived at read time). Analyses made before S9 return `available: false`.

**Possession & passes (S9 ball part, M3-CV-02).** Same `load_db` task (`stages.possession.run_possession`; the task
now also gets `shots` so nothing chains across a cut) → `stats_team` per team (`pv-possession/v1`: `possession_ms`,
`possession_pct`, `thirds_ms` {left, middle, right as filmed}, `timeline_ms` per video minute, `passes`,
`passes_completed`, `balls_lost`, `pass_list` ≤ 5000) and per-track `touches`, `possession_s`, `passes`,
`passes_completed`, `received` merged into `stats_player`. Ball cleaning: detections chained while ≤ 40 m/s
(+1 m), chains < 0.2 s dropped. Contact = nearest player / goalkeeper of a team within 2.5 m (FR-406 says 2 m; both
ground points carry ~0.5–0.7 m of noise). Spell = one track's contacts ≤ 0.4 s apart spanning ≥ 0.4 s (≥ 3
contacts); overlaps go to the stronger; a < 0.8 s spell of the other team between two of one team is a duel.
Spells ≤ 5 s apart in one shot: same team → completed pass (≥ 2 m of ball travel), other team → lost ball.
Possession = own spells + completed passes in flight. API: `/stats` teams carry them + `possession_timeline`;
`GET /results/{v}/passes?cluster=&completed=`. Team = the S5 track team (later SCR-14 team fixes don't re-assign
possession; physical totals do).

**Data exports (M3-BE-03).** `POST /api/v1/exports {kind: "data", data_format: json|csv, rate_hz: 5|25, from_ms,
to_ms}` (any plan; quality / watermark don't apply). The API freezes into `ExportSpec` the shots, the video title /
duration and the stats as `GET /stats` (min_observed_s 0) + `/passes` return them; `cpu.render_export` branches to
`pitchvision_worker.data_export` → `keys.export_key(…, "json"|"zip")`. Schema `pitchvision.data/v1`: tracking rows
`t_ms, frame, shot_type, track_id, team, role, x_m, y_m, h_quality, estimated, conf, box_*` (team / role with the
SCR-14 fixes; x/y empty when h_quality < 0.5; estimated = S5-interpolated) + teams (possession, passes, observed
distance, `distance_match_estimated_m`), players (per track), passes, shots, possession timeline. JSON = one
document (tracking as columns + rows); CSV = ZIP of tracking / teams / players / passes / shots CSVs + README +
meta.json. Migration 0012 widens `exports.kind`.

**Stills.** `cpu.extract_frame {org_id, video_id, t_ms}` (`pitchvision_worker.frames`): t snapped to the 25 fps grid,
ffmpeg input-seeks normalized.mp4 through a 5-min presigned URL, JPEG cached at `keys.frame_key` (`media/…/frames/
{t_ms:09d}.jpg`, erased with the video). The API calls it synchronously (`PV_FRAME_TASK_TIMEOUT_S`, 20 s) only when
the object is missing.

**Pitch template.** `packages/schemas/pitchvision_schemas/pitch_template.json` is generated from
`cv_core.pitch.template` (`uv run python -m cv_core.pitch.export_template [--check]`, drift-tested in cv-core) and
served by `GET /api/v1/results/pitch-template` (the API never imports cv_core).

API (all under `/api/v1/results/{video_id}`, problem+json, RLS; writes editor+, reads viewer+): `GET /teams`
(+ `sample_sheet` presigned URL + per-team `samples` rectangles), `PATCH /teams`, `GET /teams/candidates`,
`POST /teams/manual` (Idempotency-Key, 202 JobOut), `GET|POST /calibration` (POST: Idempotency-Key, 202
`{job, correction_id, revision}`), `GET /calibration/frame?t_ms=`, `GET /media` (presigned normalized.mp4,
`PV_MEDIA_URL_TTL_S` 6 h); plus `GET /api/v1/results/pitch-template`.

## SCR-14 E05 / E06 — attacking direction, goalkeeper / referee fixes
- `teams.attack_h1` (`left` | `right`, the side the team attacks in the first half as the main camera sees it) +
  `attack_source` (`auto` | `user`), API migration 0007. S8 (`load_db.guess_attack`) guesses it: in the first half
  of the analysed time each team's goalkeeper stands in front of its own goal; position = `px` when most rows have
  it, else the box centre on the image (main camera on a touchline ⇒ image left/right = pitch left/right). Both
  goalkeepers must agree and need ≥ 25 rows, else NULL. A re-run never overwrites `user`. `PATCH /teams`
  `attack_h1` = cluster 0's side (cluster 1 gets the other). Manifest `teams[].attack_h1`.
- `players.frames / t0_ms / t1_ms` (on-screen time per global track, S8). `GET /results/{v}/players?role=…`
  (longest first), `PATCH /results/{v}/players {changes: [{track_id, role, team}]}` (editor+, recorded as a
  `players` correction) sets role + team and `confirmed`. Display-only: tiles / positions keep the detection; the
  manifest's `track_overrides [{track_id, team (-1 = none), cls}]` (confirmed players) are applied by the web
  player (overlay + map) and frozen into `ExportSpec.track_overrides` for S11. A re-run keeps confirmed players.

## Exports — S11 render (M1-BE-17, SCR-25 / SCR-26)
Outside the job DAG. `POST /api/v1/exports` (Idempotency-Key; any member) freezes a
`pitchvision_schemas.ExportSpec` in `exports.spec` — latest done job + revision, range `[from_ms, to_ms)` (omitted =
full match), layers (boxes / labels / conf / ball / trails), layout `video` | `video_map` (refused with 422
`no_pitch_map` when pitch calibration is `unavailable`), `out_height` 720 | 1080 (1080 → 402 `PLAN_REQUIRED` on
Free), `watermark` + `ttl_days` from the plan (`pitchvision_schemas.plans`), team names / colours from the teams
table — and publishes `cpu.render_export {export_id, org_id}`.

Worker (`pitchvision_worker.exports` + `export_render`): guarded `queued → running`; ffmpeg decodes normalized.mp4
(presigned, input-seek) at the output size, frames are painted with OpenCV like the web overlay and piped to libx264
(yuv420p, CRF 21, faststart) with the range's audio; output frame `i` = normalized frame `f0 + i` (`f0 = ⌈from_ms ·
fps / 1000⌉`). `progress` + `heartbeat_at` every ~2 s, guarded on `running` — a cancel / delete stops the render
and nothing is uploaded. Output `keys.export_key(org, video, export_id, "mp4")`; `done` sets `expires_at = now +
ttl_days`. Errors → `exports.error {code: VIDEO_UNREADABLE | EXPORT_FAILED | INTERNAL, retryable, detail}`; no
automatic retry (SCR-26 Retry re-queues the same row). `cpu.sweep_exports` (beat, `PV_EXPORT_SWEEP_S`, 10 min) fails
`running` rows without a heartbeat for 10 min (retryable) and deletes files of expired / deleted exports
(`object_key → NULL`). API reads: `GET /exports?status=all|active|done&cursor` (done past `expires_at` reads
`expired`), `GET /exports/{id}/download` (presigned attachment, 15 min; 409 `not_ready`, 410 `expired`), `POST
…/cancel`, `POST …/retry`, `DELETE` (requester or editor+). 1080p reads the original upload (`videos.object_key`) through S2's frame
selection (`cv_core.io.normalize.frame_select_filter`, seek offset added back) so frames match the tracks; it
falls back to upscaling normalized.mp4 when the original is missing or not larger.

## Notifications (M1-BE-19, SCR-51)
`notifications` (API migration 0006, RLS) — one row per recipient; `type` ∈ `pitchvision_schemas.NotificationType`,
`path` (web route) from `notification_path`. Written in the SAME transaction as the change they report, with a
unique `(user_id, dedupe_key)`:
- orchestrator, job → done: `needs_teams` (teams not all confirmed; not after a recalibration) else `job_ready`;
  job → failed: `job_failed {code, retryable}`. Recipient `jobs.created_by`.
- export task, done / failed: `export_ready {filename, size_bytes}` / `export_failed {code}` to `exports.created_by`.
- API quota check, when a new / retried job's minutes cross 80 % / 100 % of the month: `quota_80` / `quota_100` to
  the caller (once per month and mark).
API: `GET /api/v1/notifications?limit&cursor` → `{items[{id, type, video_id, video_title, data, path, created_at,
read_at}], unread_count, next_cursor}` (own rows in the active org), `POST /api/v1/notifications/read {ids | all}`.
Email: types in `EMAIL_TYPES` start `email_status = pending` (others `skipped`); `cpu.deliver_notification_emails`
(beat, `PV_NOTIFY_EMAIL_S` 60 s) posts them to identity-svc `POST /internal/v1/notifications/email` (bearer
`PV_INTERNAL_TOKEN`; identity checks membership + verified email and renders the copy) → `sent` / `skipped`;
transport errors retry, `failed` after 5 attempts or a 4xx. No token / URL configured → `skipped` (in-app only).
`data_export_ready` (→ `/settings/privacy`) always emails. `member_joined {name, org_name, role, user_id}` (→
`/settings/members`, in-app only): the same beat first pulls identity's member events for `PV_REGION` and writes one
row per recipient (dedupe `member_joined:{event id}`), then acks.

## Saved teams (SCR-40 O03, SCR-14 E02; API migration 0009)
`saved_teams {name, color, uses, last_used_at}` per org, unique on `lower(name)`. `PATCH /results/{v}/teams` with
`confirmed: true` upserts both clusters' names except the defaults "Team A" / "Team B" (latest colour wins,
`uses + 1`). `GET /api/v1/saved-teams` (any member, most used first, ≤ 200) feeds the SCR-14 name suggestions —
picking one applies its colour; `PATCH /saved-teams/{id} {name?, color?}` (admin+, 409 `name_taken`), `DELETE`
(admin+). Videos keep their own names. An org erasure deletes them (`pv_purge_org` re-created in 0009).

## Video deletion (AD §14)
`DELETE /api/v1/videos/{id}` cancels the active job (→ `orchestrate.cancel_job`), soft-deletes the row and enqueues
`cpu.delete_video_objects(org_id, video_id)` (`pitchvision_worker.erasure`): every object under
`keys.video_prefixes` + in-progress multipart uploads, with a final pass 4 h later for outputs of stage tasks that
were mid-flight. Stages must therefore only write under the keys above.

## Data & privacy (SCR-40 D02–D04, API migration 0008)
**Data export (D02).** `POST /api/v1/privacy/data-exports` (any member; 409 `in_progress` while the caller's last
export is queued / running and < 1 h old) → `data_exports {scope: org (admin+) | account}` + `cpu.build_data_export
{export_id, org_id}`. The worker (`pitchvision_worker.privacy`) zips `README.txt`, `account.json` (identity
`GET /internal/v1/users/{id}/export`), `notifications.json` (own), `org/{table}.json` (scope `org`: every row of
videos, jobs, teams, players, corrections, shots, exports, stats, tactics, reports; scope `account`: only rows with
`created_by` = the user) and, scope `org`, `org/positions/{video}.csv` (from `positions_org`). No video files. Upload
to `keys.data_export_key(org, id)` (`exports/orgs/{org}/data/{id}.zip`), `done` + `data_export_ready` notification
in one transaction; `expires_at` = +7 days. `GET /api/v1/privacy` → `{videos, exports, data_export}` (DLG-08
counts + the caller's latest export, `expired` past `expires_at`); `GET /privacy/data-exports/{id}/download` →
presigned 15 min, requester only (409 `not_ready`, 410 `expired`).

**Org / account erasure (D03 / D04).** Identity deletes and records an erasure request (identity-api.md "Erasure").
`cpu.privacy_sweep` (beat, `PV_PRIVACY_SWEEP_S`, 10 min) deletes expired archives
(`pv_data_export_purge_candidates`), then pulls `GET /internal/v1/erasures?region=PV_REGION` and per request:
- pass = org: `pv_purge_org(org)` (SECURITY DEFINER, pv_worker only: positions + every tenant table) and every
  object under `keys.org_prefixes(org)` (multipart uploads aborted); user: `pv_purge_user(user)` (notifications and
  data exports deleted, `created_by` cleared on videos / jobs / corrections / exports) and the archives' files.
- first pass at once, final pass ≥ 4 h later (stage tasks in flight, access tokens minted before the delete live
  15 min), then `POST /internal/v1/erasures/{id}/done`. Progress in `erasures` (no RLS, pv_worker only); a request
  still listed after the ack is acked again. Without `PV_IDENTITY_INTERNAL_URL` / `PV_INTERNAL_TOKEN` nothing runs.
