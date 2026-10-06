# PitchVision AI

Web SaaS phân tích video bóng đá **broadcast**. Dán link YouTube hoặc upload file trận đấu, hệ thống sẽ:
- phát hiện và theo dõi cầu thủ, trọng tài, bóng;
- chia hai đội;
- dựng tactical map 105 × 68 m đồng bộ từng khung với video;
- đánh dấu replay/cận cảnh là "không phân tích";
- về sau tính thống kê và viết báo cáo AI.

Thị trường quốc tế, giao diện EN trước. GPU chạy hybrid: tự host RTX 4090 hoặc thuê cloud (RunPod).

> Trạng thái: **M1 đang làm**. Gate G0 đã duyệt 05/10/2026. Alpha 04/12/2026 · public beta 26/01/2027 · 1.0 26/03/2027.
> Tiến độ chi tiết ở [`docs/PLAN.html`](docs/PLAN.html), mục "Trạng thái thực hiện". Mục này sinh từ [`docs/status.json`](docs/status.json).

---

## Mục lục
- [Tài liệu](#tài-liệu)
- [Kiến trúc tóm tắt](#kiến-trúc-tóm-tắt)
- [Cấu trúc repo](#cấu-trúc-repo)
- [Yêu cầu](#yêu-cầu)
- [Chạy nhanh bằng Docker Compose](#chạy-nhanh-bằng-docker-compose)
- [Phát triển](#phát-triển)
- [Model và dữ liệu](#model-và-dữ-liệu)
- [Kiểm thử và CI](#kiểm-thử-và-ci)
- [Quyết định đã chốt](#quyết-định-đã-chốt)
- [Quy tắc bắt buộc](#quy-tắc-bắt-buộc)

---

## Tài liệu

| Tài liệu | Nội dung |
|---|---|
| [`docs/PRD.html`](docs/PRD.html) | Yêu cầu sản phẩm (v1.3) |
| [`docs/AD.html`](docs/AD.html) | Kiến trúc (v1.2, đã chỉnh 9.2 và 6.5): container, pipeline CV, dữ liệu, API, bảo mật, ADR |
| [`docs/UIUX.html`](docs/UIUX.html) | Màn hình SCR-xx, luồng, quyền theo vai trò |
| [`docs/prototype/index.html`](docs/prototype/index.html) | Prototype bấm được, nguồn design token cho web |
| [`docs/PLAN.html`](docs/PLAN.html) | Kế hoạch M1–M4, Gate, rủi ro, trạng thái thực hiện |
| [`docs/contracts/identity-api.md`](docs/contracts/identity-api.md) | Hợp đồng auth/org: endpoint, JWT, cookie, mã lỗi |
| [`docs/contracts/pipeline-stages.md`](docs/contracts/pipeline-stages.md) | Hợp đồng giữa orchestrator và các stage CV |
| [`infra/compose/README.md`](infra/compose/README.md) | Chạy stack local / self-host |
| [`infra/terraform/README.md`](infra/terraform/README.md) | R2 bucket, CORS, lifecycle, token |
| [`training/README.md`](training/README.md), [`eval/README.md`](eval/README.md) | Huấn luyện / export model, golden set và đo chất lượng |

File HTML dùng mermaid từ CDN, nên cần mạng. Mở bằng `python3 -m http.server` trong `docs/`.

## Kiến trúc tóm tắt

```
Browser (React SPA) ──► web (nginx) ──┬─► identity-svc (FastAPI, global): tài khoản, tổ chức, JWT RS256 + JWKS
                                      └─► api (FastAPI, theo region): videos, jobs, WebSocket tiến trình
                                                │ Celery (Redis)
                    ┌───────────────────────────┼─────────────────────────────┐
                    ▼                           ▼                             ▼
             ingest-worker              orchestrator (DAG)             cpu-worker / gpu-worker
             S1 YouTube (yt-dlp)        job_tasks, retry,              S2 chuẩn hoá · S2b cảnh · S3 phân đội
                                        ComputeBackend                 S4 detect + track (cv-core, ONNX/TensorRT)
                    └──────────────► Object storage S3 (R2 cloud / SeaweedFS local) ◄───┘
                                      Postgres 16 + TimescaleDB (RLS theo tổ chức)
```

**Pipeline offline** (AD §6, chi tiết trong [`pipeline-stages.md`](docs/contracts/pipeline-stages.md)):

| Stage | Việc |
|---|---|
| S1 ingest | Upload multipart thẳng từ trình duyệt lên kho, hoặc tải từ YouTube |
| S2 | Chuẩn hoá về 25 fps, cạnh dài 1280, keyframe mỗi 2 s |
| S2b | Cắt cảnh và phân loại `main_wide` / `close_up` / `replay` / `other` |
| Lập segment | Chỉ lấy cảnh toàn cảnh, ghép thành segment khoảng 120 s |
| S3 | Mô hình phân đội: SigLIP + spectral 2 cụm |
| S4 | D-FINE + lọc người ngoài sân + ByteTrack + gán đội theo track, ghi `detections.parquet` |
| S5, S7, S8 | Ghép track, tile 10 s, nạp DB 5 Hz (M1 W4) |

**Phát lại:** trình duyệt dùng `requestVideoFrameCallback` để đồng bộ overlay và tactical map theo từng khung (SP-08).

## Cấu trúc repo

```
apps/
  api/          FastAPI theo region (pitchvision_api): videos, uploads, jobs, ws, Alembic
  identity/     FastAPI global (pitchvision_identity): auth, orgs, invites, Google OIDC, Alembic riêng
  web/          React 18 + Vite + Tailwind + Radix, TanStack Router/Query, i18next
packages/
  schemas/      Hợp đồng dùng chung (Pydantic): PipelineConfig, GpuTask, Job state, WS, Tile, Manifest, keys
  cv-core/      Thư viện CV thuần: runtime ONNX/TensorRT, VideoReader, normalize, shots, detect, teams, track, pitch
workers/        pitchvision_worker: Celery roles (orchestrator, cpu, gpu, ingest), orchestrator, compute, stages
eval/           Golden set (golden.json), metric (mAP, phân đội, cảnh, homography), script đánh giá
training/       Export ONNX + fine-tune (D-FINE, YOLOv11-pose, shot GBM); project uv riêng, không thuộc workspace
models/         registry.yaml + model card (trọng số KHÔNG nằm trong git)
infra/
  compose/      docker-compose.yml + .env.example (profile gpu-local, live)
  docker/       Dockerfile.api / .worker (CUDA 12.4 + TensorRT) / .web, entrypoint
  terraform/    Cloudflare R2: bucket theo region (EU jurisdiction), CORS, lifecycle, token
docs/           PRD, AD, UIUX, PLAN, prototype, contracts, status.json
spikes/         Script spike Phase 0 (SP-02…SP-11) + kết quả JSON; chỉ để tham khảo
data/golden/    Manifest golden set (video gốc KHÔNG nằm trong git)
```

**Ranh giới phụ thuộc** (import-linter kiểm trong CI):
- `schemas` không phụ thuộc gì trong repo.
- `cv-core` không import web, queue hay DB.
- `api` không import `cv-core` hay `workers`, để image API không kéo theo CUDA.
- `identity` tách hẳn khỏi `api`, `cv-core` và `workers`.

## Yêu cầu

| Công cụ | Phiên bản |
|---|---|
| Python | 3.12 (uv tự cài theo `.python-version`) |
| [uv](https://docs.astral.sh/uv/) | ≥ 0.9 |
| Node | ≥ 22 |
| pnpm | 10.33 (khoá trong `package.json`) |
| Docker + Compose v2 | Docker Desktop nên cấp ≥ 8 GB RAM |
| ffmpeg / ffprobe | Cho cv-core và test |
| NVIDIA driver ≥ 550 + NVIDIA Container Toolkit | Chỉ cần trên máy GPU |

## Chạy nhanh bằng Docker Compose

```bash
cd infra/compose
cp .env.example .env && chmod 600 .env       # sửa cổng nếu máy đã dùng 3000/5432/6379/8000/8001
docker compose up -d --build                  # Mac / CPU: PV_WORKER_BASE=python:3.12-slim, PV_LOCAL_GPU_QUEUE=cpu
docker compose ps                             # mọi dịch vụ chạy lâu phải "healthy"
```

| Dịch vụ | Mặc định |
|---|---|
| Web (SPA + proxy `/api`) | http://localhost:3000 |
| API `/healthz` | http://localhost:8000 |
| Identity | http://localhost:8001 |
| Mailpit (email xác minh/mời) | http://localhost:8025 |
| S3 local | http://localhost:8333 |

- **Đổi cổng web:** đặt `PV_WEB_URL` cho khớp. Biến này là `iss` của JWT, link trong email, và origin CORS của kho.
- **Model cho S2b/S3/S4:** mount từ `PV_MODEL_DIR` (mặc định `~/.cache/pitchvision/models`); cách tạo ở mục [Model và dữ liệu](#model-và-dữ-liệu).
- **Máy RTX 4090:** đặt `PV_WORKER_BASE=nvidia/cuda:12.4.1-runtime-ubuntu22.04`, `PV_LOCAL_GPU_QUEUE=gpu`, rồi chạy `docker compose --profile gpu-local up -d --build`.
- **RAM:** mỗi tiến trình worker chạy stage GPU tốn khoảng 1,5 GB. Giữ `PV_CPU_CONCURRENCY` nhỏ; mặc định là 2.

Chi tiết (reset volume, xem log, đổi kho S3) ở [`infra/compose/README.md`](infra/compose/README.md).

## Phát triển

### Python (uv workspace)

```bash
uv sync                                   # tạo .venv cho cả workspace
uv run ruff check packages apps workers eval
uv run mypy packages/schemas packages/cv-core/cv_core apps/api/pitchvision_api \
            apps/identity/pitchvision_identity workers/pitchvision_worker
uv run lint-imports                       # ranh giới phụ thuộc (AD §5)
uv run pytest -q                          # test DB/S3/Redis tự bật container Docker; không có Docker thì skip
```

**Sinh hợp đồng khi đổi schema hoặc API:**

```bash
uv run python -m pitchvision_schemas.export                 # packages/schemas/jsonschema/*.json
uv run python -m pitchvision_api.openapi > apps/api/openapi.json
uv run python -m pitchvision_identity.openapi > apps/identity/openapi.json
pnpm -C apps/web gen                                         # client/type TS cho web
```

CI fail nếu bất kỳ file sinh ra nào cũ hơn nguồn.

**Migration:**
- API: `apps/api/alembic.ini`. Chạy bằng owner, qua `PV_MIGRATE_DATABASE_URL`.
- Identity: `apps/identity/alembic.ini`, DB riêng.
- API runtime kết nối bằng role `pv_app` (không bypass RLS).

### Web

```bash
pnpm install
pnpm -C apps/web dev            # http://localhost:5173 (đặt VITE_API_URL / VITE_IDENTITY_URL nếu không qua nginx)
VITE_MOCK_API=1 pnpm -C apps/web dev   # chạy với MSW mock, không cần backend
pnpm web:check                  # gen:check + lint + typecheck + test + build
```

Hai trang chỉ có ở bản dev:
- `/__ui`: gallery component, hiện cả dark và light.
- `/__player`: thử sync engine với video local.

### Quy ước API
- Tiền tố `/api/v1`, trường `snake_case`, thời gian ISO-8601 UTC, toạ độ sân tính bằng mét.
- Lỗi theo RFC 9457 (`application/problem+json`, có trường `code`).
- `POST /jobs` bắt buộc header `Idempotency-Key`.
- WebSocket `/api/v1/ws?token=` subscribe topic `job:{id}` (AD §10.3).

## Model và dữ liệu

| Model | Dùng cho | Giấy phép | Tạo bằng |
|---|---|---|---|
| D-FINE-M obj2coco (ONNX) | Detect người/bóng (S2b, S3, S4) | Apache-2.0 | `training/export_dfine_onnx.py`; fine-tune: `training/dfine/` (cần GPU) |
| SigLIP-base p16-224 (ONNX) | Embedding áo để phân đội (S3/S4) | Apache-2.0 | `training/export_siglip_onnx.py` |
| Shot GBM (JSON) | Phân loại cảnh (S2b) | Nội bộ | `training/train_shots.py` |
| YOLOv11-pose 32 keypoint | Keypoint sân → homography (M1-CV-05) | **Ultralytics Enterprise** | `training/keypoints/` (cần GPU + nhãn golden) |

- **Trọng số:** nằm ở `~/.cache/pitchvision/models` (máy dev) hoặc bucket models trên R2 (cloud). [`models/registry.yaml`](models/registry.yaml) ghi tên, phiên bản, sha256 và giấy phép; checksum được kiểm khi nạp.
- **Golden set:** 19 trận broadcast, mỗi trận 15 phút, mô tả trong [`eval/golden.json`](eval/golden.json) và [`data/golden/manifest.json`](data/golden/manifest.json). File video **chỉ dùng nội bộ R&D**, không có trong git.

## Kiểm thử và CI

[GitHub Actions](.github/workflows/ci.yml) chạy trên mỗi PR và mỗi lần push lên `main`:

| Job | Nội dung |
|---|---|
| python | ruff, mypy, import-linter, pytest (Postgres/TimescaleDB thật) |
| schemas | JSON Schema đúng với nguồn |
| web | lint, typecheck, test, gen:check, build |
| compose | `docker compose config` |
| images | build `pitchvision-api`, `pitchvision-worker` (CUDA 12.4 + TensorRT), `pitchvision-web`; push lên GHCR với tag git sha khi vào `main` |

Test chất lượng CV cần dữ liệu golden và chỉ chạy khi có biến môi trường, ví dụ `PITCHVISION_GOLDEN_DIR=$PWD/data/golden uv run pytest -q`. Kết quả đánh giá (chỉ số, không có ảnh) nằm trong `eval/results/`.

## Quyết định đã chốt

- **Broadcast-first.** RTSP live, camera riêng và sân 9v9/7v7 để sang Phase C, sau bản 1.0.
- **Detector:** D-FINE (Apache-2.0), không dùng Ultralytics. Ultralytics Enterprise là phương án dự phòng (SP-11).
- **Keypoint sân:** YOLOv11-pose dưới giấy phép Ultralytics Enterprise, phải ký trước public beta.
- **Lưu trữ:** Cloudflare R2, bucket theo region; EU dùng `jurisdiction=eu`. Key đặt lớp lưu trữ lên đầu (`source/…`, `segments/…`) để rule lifecycle lọc theo tiền tố được (AD 9.2 v1.3).
- **`h_quality`:** đo độ khớp vạch sân, không dùng sai số fit keypoint (bài học SP-03).
- **Bảng `positions`:** là hypertable nén, nên không bật được RLS; chỉ đọc qua view `positions_org` đã lọc theo tổ chức.

**Chờ quyết:**
- Kho S3 cho bản self-host: **SeaweedFS hay Garage**. Image MinIO không còn kéo công khai được; local tạm dùng SeaweedFS.
- Giấy phép dữ liệu Objects365 và SoccerNet (chỉ cho nghiên cứu), xử lý trong G-LEGAL.

## Quy tắc bắt buộc

- **Không commit:**
  - `.env` và mọi secret;
  - video broadcast, frame trích từ chúng (`data/golden/raw`, `spikes/results/**/*.jpg`);
  - trọng số model (`*.onnx`, `*.pt`, `*.engine`).

  `.gitignore` đã chặn các loại này, nhưng hãy kiểm lại `git status` trước khi commit.
- **Không dùng cookie trình duyệt cho yt-dlp.** YouTube có bot-check; yt-dlp pin ở bản ≥ 2026.08.19 (SP-10).
- **Đổi hợp đồng thì sửa mọi bên trong cùng PR.** Hợp đồng gồm `packages/schemas`, `docs/contracts/*` và OpenAPI; các phía liên quan là API, worker, web.
- **Cập nhật trạng thái sau mỗi bước hoàn thành:** sửa `docs/status.json` rồi chạy `python3 docs/update_status.py` để render vào PLAN.
