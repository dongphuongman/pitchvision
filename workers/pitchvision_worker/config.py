"""Worker runtime config from the same PV_* env vars as the API (compose x-pv-env)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_DB_URL = "postgresql+psycopg://pv_app:pv_app@localhost:5432/pitchvision"


@dataclass(frozen=True)
class WorkerConfig:
    database_url: str
    redis_url: str
    storage_endpoint: str | None
    storage_bucket: str
    storage_region: str
    storage_access_key: str | None
    storage_secret_key: str | None
    max_video_duration_s: int
    # --- pipeline (M1-BE-10/11) ---
    image_tag: str = "dev"  # PV_IMAGE_TAG: tasks for another tag are refused (AD §7 deterministic results)
    local_gpu_queue: str = "gpu"  # PV_LOCAL_GPU_QUEUE: `cpu` on a CPU-only box runs the GPU stages slowly
    compute_backends: tuple[str, ...] = ("local",)  # PV_COMPUTE_BACKENDS, priority order (AD §7.1)
    max_inflight_segments: int = 16  # PV_MAX_INFLIGHT_SEGMENTS per job (capacity also caps it)
    sweep_interval_s: float = 30.0  # PV_SWEEP_INTERVAL_S: orchestrator recovery sweep period
    # --- notifications (M1-BE-19): identity-svc sends the emails; unset = in-app only
    identity_internal_url: str | None = None  # PV_IDENTITY_INTERNAL_URL, e.g. http://identity:8001
    internal_token: str | None = None  # PV_INTERNAL_TOKEN, shared with identity-svc

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> WorkerConfig:
        e = os.environ if env is None else env
        return cls(
            # pv_worker (migration 0004, RLS-bound); see repository.py for why workers write through it
            database_url=sync_url(e.get("PV_WORKER_DATABASE_URL") or e.get("PV_DATABASE_URL") or DEFAULT_DB_URL),
            redis_url=e.get("PV_REDIS_URL") or "redis://localhost:6379/0",
            storage_endpoint=e.get("PV_STORAGE_ENDPOINT") or None,
            storage_bucket=e.get("PV_STORAGE_BUCKET") or "pv-local-data",
            storage_region=e.get("PV_STORAGE_REGION") or "auto",
            storage_access_key=e.get("PV_STORAGE_ACCESS_KEY") or None,
            storage_secret_key=e.get("PV_STORAGE_SECRET_KEY") or None,
            max_video_duration_s=int(e.get("PV_MAX_VIDEO_DURATION_S") or 3 * 3600),
            image_tag=e.get("PV_IMAGE_TAG") or "dev",
            local_gpu_queue=e.get("PV_LOCAL_GPU_QUEUE") or "gpu",
            compute_backends=tuple(
                b.strip() for b in (e.get("PV_COMPUTE_BACKENDS") or "local").split(",") if b.strip()
            ),
            max_inflight_segments=int(e.get("PV_MAX_INFLIGHT_SEGMENTS") or 16),
            sweep_interval_s=float(e.get("PV_SWEEP_INTERVAL_S") or 30),
            identity_internal_url=(e.get("PV_IDENTITY_INTERNAL_URL") or "").rstrip("/") or None,
            internal_token=e.get("PV_INTERNAL_TOKEN") or None,
        )


def sync_url(url: str) -> str:
    """Workers are sync (Celery prefork): use psycopg 3 even when given the API's asyncpg DSN."""
    for prefix in ("postgresql+asyncpg://", "postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url.removeprefix(prefix)
    return url
