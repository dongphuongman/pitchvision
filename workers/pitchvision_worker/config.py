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

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> WorkerConfig:
        e = os.environ if env is None else env
        return cls(
            # RLS-bound role (pv_app today); see repository.py for why workers write through it
            database_url=sync_url(e.get("PV_WORKER_DATABASE_URL") or e.get("PV_DATABASE_URL") or DEFAULT_DB_URL),
            redis_url=e.get("PV_REDIS_URL") or "redis://localhost:6379/0",
            storage_endpoint=e.get("PV_STORAGE_ENDPOINT") or None,
            storage_bucket=e.get("PV_STORAGE_BUCKET") or "pv-local-data",
            storage_region=e.get("PV_STORAGE_REGION") or "auto",
            storage_access_key=e.get("PV_STORAGE_ACCESS_KEY") or None,
            storage_secret_key=e.get("PV_STORAGE_SECRET_KEY") or None,
            max_video_duration_s=int(e.get("PV_MAX_VIDEO_DURATION_S") or 3 * 3600),
        )


def sync_url(url: str) -> str:
    """Workers are sync (Celery prefork): use psycopg 3 even when given the API's asyncpg DSN."""
    for prefix in ("postgresql+asyncpg://", "postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url.removeprefix(prefix)
    return url
