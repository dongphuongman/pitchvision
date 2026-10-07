---
status: in-progress
branch: main → origin https://github.com/dongphuongman/pitchvision (private), everything pushed; check the last CI run
timestamp: 2026-10-07 (evening)
---

# PitchVision AI — handoff

Web SaaS analysing football **broadcast** video: YouTube URL / file upload → detection, team split, tracking,
tactical map, stats, AI report. Read `README.md` first (layout, commands, rules), then `docs/PLAN.html` §
"Trạng thái thực hiện" (rendered from `docs/status.json`).

## Where we are (M1, plan 12/10–20/11, alpha 04/12/2026)
Done and on `main` (Python 913 tests, web 267 tests + 5 Playwright flows; Docker images built in CI):
- Phase 0 spikes + Gate G0; SP-07 on real R2; SP-08 rVFC sync; SP-11 detector choice.
- W1 foundation: uv + pnpm monorepo, import-linter, `packages/schemas`, Alembic + RLS, FastAPI, cv-core runtime/VideoReader, golden eval, web shell, Compose, Dockerfiles, GitHub Actions.
- W2: identity-svc (auth, orgs, roles, invites, Google OIDC, JWKS), API JWT, presigned multipart upload, YouTube ingest (queue `ingest`), S2 normalise, shot classifier 97.9 %, D-FINE ONNX, keypoint/homography lib, auth/onboarding screens, Terraform R2.
- W3: jobs API + orchestrator DAG (job_tasks, retries, recovery) + ComputeBackend + WebSocket; S2b segments, S3 teams (SigLIP + spectral), S4 D-FINE + own ByteTrack; Library + sync engine.
- W4: S5 stitch, optional pitch stage, S7 signed tiles (HMAC), S8 positions via `pv_worker` role, manifest; SCR-11 New analysis (YouTube + resumable upload), SCR-12 settings, upload tray, SCR-13 Processing.
- W5: SCR-20 workspace (manifest + `/media`), SCR-14 team confirm/manual (team_rerun job), SCR-15 manual calibration (recalibrate job, CPU recompute, new revision).

- W6: plans Free/Pro/Team (identity `subscriptions`, token claims `plan` / `quota_min`, `pitchvision_schemas.plans`);
  S11 export render + `/api/v1/exports` + SCR-25 modal + SCR-26 page (M1-BE-17); monthly minute quota + `GET /usage`
  + G.E06 meter (M1-BE-18); notifications table + `/api/v1/notifications` + SCR-51 bell + emails via identity
  `/internal/v1/notifications/email` (M1-BE-19); test for recalibrating after HOMOGRAPHY_UNAVAILABLE (backend was
  already right: an `analyze` job never fails on the pitch); SCR-14 E05 attacking direction (auto-guessed by S8
  from the goalkeepers, `PATCH /teams attack_h1`) + E06 goalkeeper / referee fixes (`/results/{v}/players`,
  manifest `track_overrides`, applied by the web player and S11); SCR-40 Settings (profile, organization,
  members, billing, email notification toggles; sections hidden by role) + SCR-41 upgrade as a waitlist
  (identity `PATCH /me`, `/auth/password/change`, `/auth/sessions/revoke-others`, `/orgs/{id}/upgrade-requests`
  — a non-owner's request emails the owners); opened from the minutes meter, 1080p export, quota 402; SCR-01
  landing at `/` (signed-in → library; Choose Pro → sign-up `plan=pro` → SCR-41 after onboarding); SCR-50 /403;
  Playwright e2e F1 / F3 / F4 / F8 against the dev server in mock-API mode (`pnpm web:e2e`, CI job `e2e`).
- Compose smoke test on the Mac (2026-10-06): a real 20 s broadcast clip ran register → Mailpit verify → org →
  multipart upload → the whole pipeline on CPU (S4 ~7 min) → manifest → export (5 s) → download → in-app + email
  notifications. Fixed on the way: stage tasks of cancelled jobs ran after a worker restart (now skipped), ball
  trails jumping across the frame, 1080p exports now read the original upload.

- SCR-40 Data & privacy (2026-10-07): D02 export all data (zip: account from identity + org tables / positions CSV
  for admin+, own rows otherwise; emailed `data_export_ready`, 7 days), D03 delete account / D04 delete org via
  DLG-08. Identity deletes at once + `erasure_requests` (migration 0004, `/internal/v1/erasures`); regional API
  migration 0008 (`data_exports`, `erasures`, `pv_purge_org` / `pv_purge_user`); worker `cpu.build_data_export` +
  `cpu.privacy_sweep` (first pass, final pass ≥ 4 h, ack). Not yet run on Compose end to end.
- Same day: `member_joined` notifications (identity `member_events`, migration 0005, pulled by the email beat),
  R02 analysis defaults on the account (`Me.analysis_defaults`, replaces the localStorage store), O03 saved teams
  (API migration 0009, `/api/v1/saved-teams`, remembered on team confirm, suggested in SCR-14).
- Responsive (UIUX §09): ☰ below 1024 px, SCR-20 Video / Map / Both below 1024 px, view-only workspace and
  "Best on a larger screen" on SCR-14/15 below 768 px; `e2e/mobile.spec.ts` checks 390 px for horizontal overflow.
  Menus are bottom sheets below 768 px (ui/DropdownMenu: `data-pv-sheet` + scrim, wrapper pinned in
  styles/index.css) and dialogs full-screen sheets below 768 px (was 640); press-and-hold / right-click on a
  library card opens its ⋮ menu (`lib/useLongPress`). Exports rows keep the visible ⋯ button only.
- Staging prep (M1-OPS-07/08): `infra/staging/` (README = runbook): one VM, Compose overlay with GHCR images,
  Caddy HTTPS on `<ip>.sslip.io` (no domain needed), Prometheus + Alertmanager (`infra/observability/alerts.yml`),
  `deploy.sh` (pull → migrate → up → readyz → smoke → rollback), `smoke.sh`, Actions `deploy-staging` (manual,
  needs STAGING_SSH_* secrets). Sentry (api / identity / workers / web via runtime `/config.js`) when DSNs are set;
  api `GET /metrics` (bearer `PV_METRICS_TOKEN`), `/readyz` also checks Redis. Overlay tried on the Mac with
  `PV_SITE=localhost`: smoke 8/8, Prometheus scraping, rules loaded. No VM exists yet.

Contracts: `docs/contracts/identity-api.md` (plans, quota), `docs/contracts/pipeline-stages.md` (stages, keys,
derived jobs, exports, notifications).

- Tactical map demo (2026-10-07, PO: "PnLCalib, demo only"): `pitch_keypoints` in the registry = PnLCalib keypoint
  HRNet exported to ONNX (`spikes/pnlcalib/`, GPL code + research-only weights git-ignored), decoded by our
  `cv_core.pitch.HeatmapPitchModel`; loaded only with `PV_ALLOW_RESEARCH_MODELS=1` + `PV_ENV=local` (staging /
  production refuse it). Highlight ESP–CZE: 28/33 wide shots usable. New derived job `repitch`
  (`POST /results/{v}/repitch`) re-runs only the pitch stage on a finished analysis. On the Mac: ~2.3 GB per
  worker process → `PV_CPU_CONCURRENCY=1`; 4.7 s / keypoint frame in Docker CPU → use `keypoint_every_n=25` for
  demos. Replace with the licensed YOLOv11-pose model before any customer run.
- Later the same day: "Map the pitch automatically" button (SCR-20 pitch banner; shown when the orchestrator
  advertises `pv:cap:pitch_model` in Redis → `GET /meta pitch_model`); camera tracking between keyframes in the
  pitch stage (`cv_core.pitch.motion`, mean h_quality 0.812 vs 0.778 interpolated); labelling tool
  `training/keypoints/label_tool.py` (writes pv-pitch-keypoints/v1, never pre-fill from research models); fixes:
  stage timeouts count from `job_tasks.running_since` (migration 0011), progress includes the pitch stage, Google
  button hidden when not configured (`GET /auth/providers`), orgs only in served regions (`PV_REGIONS`).
- M1-BE-22 hardening (2026-10-07, evening): per-user rate limits on the expensive endpoints
  (`apps/api/pitchvision_api/services/ratelimit.py`: Redis token bucket via one Lua call, fails open, 429
  RATE_LIMITED + Retry-After, `pv_rate_limited_total{rule}`; `PV_RATE_LIMITS=0` turns them off — the API test
  conftest does that, `tests/test_ratelimit.py` turns them on); ffmpeg/ffprobe children get rlimits
  (`cv_core.io.limits.child_limits`: RLIMIT_AS 2 GiB probes / 8 GiB ffmpeg, none for NVENC; no core dumps; probes
  may write no file; Linux only, `PITCHVISION_FFMPEG_MAX_MEM_MB` overrides); compose `x-hardened` (cap_drop ALL +
  no-new-privileges) on api/identity/web/workers, workers `pids_limit: 2048`. Checked on Compose: all services
  healthy, CapEff 0, normalize works under the limits.
- Manual calibration follows the camera (2026-10-07, late): `recompute` now also takes `normalized` and, for the
  calibrated shot, runs `CameraMotion` + `propagate` from the clicked frame (`track_manual_h`; decay 0.002/frame,
  unreachable frames keep the fixed H at h_quality ≤ 0.3, undecodable video → fixed H with a warning). Scope
  `video` still reuses the fixed H on other shots. Synthetic 4 px/frame pan: max error 0.18 m (fixed H > 5 m).
  Not yet run on Compose with a real broadcast clip.
- M1-CV-20 line refinement (2026-10-07, night): `cv_core.pitch.refine.refine_homography` (Gauss-Newton on the
  smoothed, truncated distance transform of the white-line mask; accepted only if line alignment rises). Pitch
  stage refines every keypoint estimate (`run_pitch(refine=True)`, `stats.estimates_refined`); recompute re-anchors
  the camera chain on the lines every 25 frames + on the clicked frame (`track_manual_h(anchor=True)`,
  `stats.line_anchors`). Real frames: mean h_quality 0.666 → 0.738 per frame; a 30 s pitch-stage run 0.552 → 0.567
  (smoothing/interpolated frames dilute it); ~50 ms per keyframe. Sloppy clicks (~6 px) → 14 px → 2.7 px over a shot.
- Compose check of the manual-calibration chain (2026-10-07, night): recalibrate job on the ESP–CZE highlight (24 s
  wide shot, 8 simulated clicks ±4 px) done in 10 s; 601/601 frames camera-tracked, 16 line anchors. Mean line
  alignment over the shot: fixed H 0.178 → camera 0.308 → camera + lines 0.521 (frames ≥ 0.5: 0 → 22 → 63 %). Done
  with `PV_DEV_AUTH=1 docker compose up -d api` (local only) instead of touching the owner's password; that video now
  serves the manual calibration (revision 1) — "Map the pitch automatically" (repitch) puts the automatic one back.
- M3 early — stats from positions (2026-10-07, night): S9 `pitchvision_worker.stages.stats` runs inside the S8
  `load_db` task → `stats_player` (per track); API `GET /results/{v}/stats` (teams summed with the current
  player team / role; `estimated_distance_m` labelled) and `GET /results/{v}/heatmap` (5 Hz positions, 2.5 m cells,
  team / track / time); web SCR-20a stats table (coverage badge, Estimated, top speed seeks) + SCR-20c Heatmaps tab
  (`?team=&track=&avg=1`). Compose ESP–CZE: 712 tracks, ~178 m per observed player-minute (highlights), top
  35.5 km/h. Old analyses show "re-analyse to get stats" (`available: false`) until a job re-runs load_db
  (recalibrate / repitch / re-analyse). Not done: possession / passes (ball, M3-CV-02), halves / phase filters,
  compare, M3-CV-04 validation vs official numbers, imperial units.
- Local Mac `.env` now has `PV_CPU_CONCURRENCY=1` + `PV_ALLOW_RESEARCH_MODELS=1` (pitch model ~2.3 GB per process).

## Next (doable on the Mac)
0. Pick up from here: label frames with the tool (needs a person, ~300 frames) → GPU run (README recipe) to replace
   PnLCalib; M3 next: possession + passes from the ball track (M3-CV-02), then validate stats vs official numbers.
1. Confirm CI is green (`gh run list --limit 1`). GitHub runners were slow on 2026-10-06 (runs queued 30–60 min).
2. Optional: Compose smoke of Data & privacy (export zip arrives in Mailpit; delete org → rows + objects gone after
   the first sweep; set `PV_PRIVACY_SWEEP_S=60` to see it quickly). D01 (training opt-in) waits for PRD Q5.
3. Staging go-live needs the PO: rent the VM (README sizes it), GHCR read token on it, Sentry project DSNs, a
   Slack/Discord webhook for alerts; then `infra/staging/deploy.sh deploy@<ip> <sha> --models`.
4. Later (needs PO): Stripe (SCR-41 Choose Pro, B03 portal), API keys (Pro).

Compose on the Mac: `cd infra/compose && docker compose up -d --build`, web at http://localhost:23000, Mailpit at
http://localhost:8025. A 20 s clip takes ~9 min on CPU (S4 ~7 min). Before a run, make sure no old job is still
active (`SELECT id, status FROM jobs WHERE status NOT IN ('done','failed','cancelled')`). Smoke script used on
2026-10-06 was a throwaway in the session scratchpad (register → Mailpit verify → upload → job → export); recreate
from the steps above if needed. Always `docker compose down` afterwards (8 GB VM).

## Blocked on the PO
- Keypoint model (tactical map): ~300 golden frames labelled (guide `training/keypoints/README.md`) + NVIDIA GPU (RunPod/4090). Until then every job reports `pitch_calibration = unavailable` (overlay works, map empty) unless calibrated manually.
- D-FINE fine-tune (M1-CV-04) needs GPU; full E2E (S4 in Docker) needs a GPU box — on the Mac S4 in containers is far too slow.
- Staging / DNS / email (M1-OPS-05): domain + Resend account.
- MinIO replacement for self-host: SeaweedFS (used locally now) vs Garage — undecided.
- G-LEGAL: Ultralytics Enterprise to sign before public beta (YOLOv11-pose keypoints); Objects365 + SoccerNet (research-only) licences.

## Decisions (PO, 05–06/10/2026)
Broadcast-first; GPU hybrid; EN-first; R2 (EU jurisdiction); detector D-FINE (Apache-2.0), Ultralytics Enterprise as fallback; keypoints YOLOv11-pose under Ultralytics Enterprise; object keys storage-class first (`source/…`, `segments/…`) so R2 lifecycle matches; `h_quality` = line alignment; `positions` (compressed hypertable, no RLS) read via `positions_org`, written by `pv_worker` with an org trigger.

## Working rules (user)
- Reply in Vietnamese ("tao/mày").
- **Max ONE background subagent at a time** — the Mac (8 GB Docker VM) OOMs. Stop compose stacks when done (`docker compose down`).
- After each completed step update `docs/status.json` and run `python3 docs/update_status.py`.
- Never commit `.env`, broadcast footage/frames (`data/golden/raw`, `spikes/results/**/*.jpg`) or weights; never use browser cookies for yt-dlp.
- Claude must not activate paid subscriptions or read secret keys from the browser — the user does those steps.

## Local environment notes
- `infra/compose/.env` (gitignored, chmod 600) uses non-default host ports: web 23000, api 28000, identity 28001, Postgres 25432, Redis 26379 (3000/5432/6379/16379/16380/8001 are taken by other projects). `PV_WEB_URL=http://localhost:23000`, `PV_WORKER_BASE=python:3.12-slim`, `PV_LOCAL_GPU_QUEUE=cpu`, `PV_CPU_CONCURRENCY=2`.
- Model weights in `~/.cache/pitchvision/models` (D-FINE ONNX, SigLIP ONNX, shot GBM json), mounted into workers via `PV_MODEL_DIR`.
- R2 spike: bucket `pitchvision-spike`, object-R/W token expiring 2026-11-04, creds in `spikes/sp07_upload/.env` (gitignored).
- Golden set: `data/golden/raw/*.mp4` (19 matches × 15 min, internal R&D only). SP-09 labels in `spikes/results/sp09/`.
- Quick checks: `uv run pytest -q`, `uv run ruff check packages apps workers eval`, `uv run lint-imports`, `pnpm web:check`, `pnpm web:e2e`.
