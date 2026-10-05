---
status: in-progress
branch: none (not a git repo)
timestamp: 2026-10-05T20:30:00+07:00
files_modified:
  - docs/PRD.html
  - docs/AD.html
  - docs/UIUX.html
  - docs/PLAN.html
  - docs/status.json
  - docs/update_status.py
  - docs/prototype/index.html
  - data/golden/ (segments.txt, dl.sh, build_manifest.py, manifest.json/csv, README.md, sheets/, raw/ 19 mp4)
  - spikes/ (sp11_detectors.py, sp08_prepare.py, sp08_run.py, sp08_overlay/, sp07_run.py, sp07_upload/, sp02_bench.py, sp03_homography.py, sp04_teams.py, sp05_tracking.py, sp09_sample.py, sp09_classify.py, botsort_gmc.yaml, results/)
---

## Working on: PitchVision AI — spikes and Gate G0

### Summary

PitchVision AI = web SaaS that analyses football broadcast video (detection, team split, tactical map, tracking, stats, AI report). All planning docs are done (PRD v1.3, AD v1.2, UIUX v1.1, clickable prototype, PLAN v1.2). Spikes were run on the Mac (Apple M4, no NVIDIA) against a 19-match broadcast golden set, and the PO approved the Gate G0 decisions. Next phase is M1 implementation starting 12/10/2026.

### Decisions Made

- Broadcast-first: YouTube URL + broadcast file upload now; live RTSP, own cameras (Veo/stand), 9v9/7v7 and Veo/Hudl import → Phase C after 1.0.
- GPU hybrid (self-host 4090 + cloud RunPod/Lambda via ComputeBackend), international EN-first, Cloudflare R2 storage (MinIO self-host).
- Perf target: ≤20 min per 90' on 1×RTX 4090 or ≥2×L4; ≥50 FPS per T4; Fast mode stride 2 ≤15 min on L4.
- New pipeline stage S2b shot segmentation; stats shown with coverage %.
- Gate G0 (approved 05/10): +2 weeks M1 for calibration → alpha 04/12/2026; +1 week M2 for offline track stitching + ReID → public beta 26/01/2027; model licences into G-LEGAL (deadline 15/01/2027); 1.0 on 26/03/2027.
- Ultralytics YOLO v8/v11 is AGPL-3.0: either buy Enterprise licence or switch to a permissive detector (RT-DETR Apache / YOLOX MIT). Decide BEFORE M1-CV-04 (week of 19/10).

### Spike results (details in docs/status.json → PLAN.html "Trạng thái thực hiện")

- SP-01 partial: 19/20 matches × 15 min in data/golden (missing J1 A4xKBpFV9Cw, YouTube bot-check; backup 6Q-q5eeMevY).
- SP-02 partial: Mac MPS ~9 fps (YOLOv11-m COCO); detection quality fine; NVIDIA/TensorRT run deferred by PO (`spikes/sp02_bench.py --backend engine`).
- SP-03 FAIL: roboflow/sports pitch-keypoint + RANSAC misaligned on most broadcast frames; self-consistency metric was misleading; use line-alignment metric + ground-truth eval.
- SP-04 DONE: SigLIP+UMAP+KMeans team split ~98% (visual check), HSV much worse.
- SP-05 partial/high risk: ByteTrack ~23 IDs/player/90 s, BoT-SORT+GMC ~18; only 15–22% of breaks caused by cuts.
- SP-09 DONE: wide-shot classifier 99.1% leave-videos-out (GBM), rule 96%; broadcast wide coverage median 71% (min 40%).
- SP-10 partial: residential IP bot-checked after ~12 GB; needs yt-dlp ≥ 2026.08.19.
- SP-11 DONE (G-LEGAL input): D-FINE-M obj2coco@640 (Apache-2.0) ≈ YOLOv11-m speed on MPS (~9 fps), better on small players and ball pre-fine-tune (more false balls at conf 0.35). Recommend D-FINE as M1-CV-04 base; Ultralytics Enterprise as fallback. Check Objects365 pretraining-data licence.
- SP-08 DONE: rVFC + round(mediaTime×25) is frame-exact (1× 100%, seek 40/40) in Chrome; rAF + currentTime is off by 1–4 frames. Safari/Firefox not tested.
- SP-07 DONE on real R2: bucket `pitchvision-spike` (CORS ExposeHeaders ETag), token Object R/W on that bucket only, expires 2026-11-04, creds in `spikes/sp07_upload/.env` (gitignored). 663 MB: 8 streams ~21 MB/s home uplink, retry + resume pass, md5 exact, test objects deleted.
- Finding: MinIO images are no longer publicly pullable (Docker Hub/quay denied) → AD "MinIO self-host" needs revisiting (SeaweedFS / Garage).
- SP-06 blocked (needs RunPod/4090).

### Remaining Work

1. DONE: PO chose D-FINE (Apache-2.0) for M1-CV-04, Ultralytics Enterprise fallback; AD/PLAN/PRD updated. Open: M1-CV-05 keypoint model is YOLOv11-pose (AGPL) — needs its own decision; Objects365 licence check. Also decide self-host storage replacement for MinIO and update AD.
2. Still open: SP-08 on Safari/Firefox, SP-02/SP-06 on NVIDIA when PO is ready.
3. Start M1 W1 (12/10): the 10 first PRs listed in PLAN section 18 (monorepo, compose, CI, schemas incl. tile + shot timeline, migrations + shots table, FastAPI skeleton, cv-core Runtime/VideoReader, golden eval, web tokens + components, router/i18n).
4. M1 W7–W8 calibration tasks M1-CV-18…22 (+ M1-FE-17, M1-BE-23); M2 extra week M2-CV-08/09.
5. PO: spot-check SP-09 labels (spikes/results/sp09/grids, labels_nonwide.json).

### Notes

- After every completed step, update docs/status.json and run `python3 docs/update_status.py` (user's explicit request).
- User writes Vietnamese with "tao/mày"; reply in Vietnamese.
- Spike env: `spikes/.venv` (uv, Python 3.12, torch MPS, ultralytics, transformers, umap, sklearn, sports, supervision). Weights in `spikes/weights/` (roboflow pitch model — spike only, AGPL).
- data/golden/raw is internal R&D only (copyrighted broadcasts); do not publish.
- Claude must not activate paid subscriptions or read secret keys from the browser; user does those steps.
- Do not use the user's browser cookies for yt-dlp without asking.
- CoreML export fails with torch 2.14 (coremltools) — dropped, not in architecture.
- OpenCV per-frame seek (cap.set each frame) corrupts tracking results — decode sequentially.
- Chrome extension tab was 'hidden' (window not visible) → video/rVFC don't run; spikes use headless Chrome via Playwright (`channel=chrome`, throwaway profile).
- Docs use mermaid from CDN (needs internet); verify in Chrome via `python3 -m http.server` (file:// not allowed by the extension).
