"""Worker tasks. Names are the contract with the API (it publishes by name, never imports this module).

- ``pitchvision_worker.ping``  smoke test
- ``ingest.probe_url``         queue ``ingest``: YouTube metadata for the preview (≤ 20 s budget on the API side)
- ``ingest.download``          queue ``ingest``: S1 download → object storage → videos row ``ready``/``failed``
"""

from __future__ import annotations

import json
import logging
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from celery import Task

from pitchvision_worker import metrics, repository
from pitchvision_worker.celery_app import QUEUE_INGEST, app
from pitchvision_worker.config import WorkerConfig
from pitchvision_worker.stages import ingest
from pitchvision_worker.storage import s3_client, source_key, upload_file

log = logging.getLogger(__name__)

TASK_PROBE_URL = "ingest.probe_url"
TASK_DOWNLOAD = "ingest.download"

RETRY_COUNTDOWNS_S = (30, 120, 300)  # AD §16.1 backoff; then the video is marked failed
ROW_WAIT_S, ROW_WAIT_TRIES = 2, 5  # the API publishes before its transaction commits


@app.task(name="pitchvision_worker.ping")
def ping() -> str:
    return "pong"


# ---------------------------------------------------------------- probe
@app.task(name=TASK_PROBE_URL, queue=QUEUE_INGEST, acks_late=False, soft_time_limit=60)
def probe_url(url: str) -> dict[str, Any]:
    return run_probe(url)


def run_probe(url: str, ydl_factory: ingest.YdlFactory = ingest.default_ydl_factory) -> dict[str, Any]:
    try:
        info = ingest.probe(url, ydl_factory)
    except ingest.IngestError as e:
        metrics.record_ingest("probe", "failed" if e.retryable else "rejected", e.reason, _redis())
        return {"ok": False, "reason": e.reason, "detail": e.detail}
    metrics.record_ingest("probe", "success", "", _redis())
    return {"ok": True, "info": info.as_dict()}


# ---------------------------------------------------------------- download
@dataclass(frozen=True)
class Outcome:
    kind: str  # done | skipped | failed | retry | wait_row
    retry_in: int | None = None
    reason: str = ""


@dataclass
class IngestDeps:
    """Everything run_download touches, injectable for tests."""

    cfg: WorkerConfig
    engine: Any
    s3: Any
    redis: Any | None
    ydl_factory: ingest.YdlFactory = ingest.default_ydl_factory
    ffprobe: Callable[[Path], ingest.MediaInfo] = ingest.ffprobe
    tmp_root: str | None = None

    @classmethod
    def from_env(cls) -> IngestDeps:
        cfg = WorkerConfig.from_env()
        return cls(cfg, repository.engine_for(cfg.database_url), _s3(cfg), _redis())


@app.task(
    name=TASK_DOWNLOAD,
    queue=QUEUE_INGEST,
    bind=True,
    max_retries=len(RETRY_COUNTDOWNS_S) + ROW_WAIT_TRIES,
    soft_time_limit=5 * 3600,
    time_limit=5 * 3600 + 600,
)
def download(self: Task, video_id: str, org_id: str, url: str, failures: int = 0, row_waits: int = 0) -> str:
    outcome = run_download(video_id, org_id, url, IngestDeps.from_env(), failures=failures, row_waits=row_waits)
    if outcome.kind in ("retry", "wait_row"):
        bump = outcome.kind == "retry"
        kwargs = {"video_id": video_id, "org_id": org_id, "url": url,
                  "failures": failures + bump, "row_waits": row_waits + (not bump)}  # fmt: skip
        raise self.retry(kwargs=kwargs, countdown=outcome.retry_in)
    return outcome.kind


def run_download(
    video_id: str, org_id: str, url: str, deps: IngestDeps, *, failures: int = 0, row_waits: int = 0
) -> Outcome:
    vid, org = uuid.UUID(video_id), uuid.UUID(org_id)
    status = repository.get_status(deps.engine, org, vid)
    if status is None:
        if row_waits < ROW_WAIT_TRIES:
            return Outcome("wait_row", ROW_WAIT_S)
        log.error("ingest: video %s/%s never became visible; dropping task", org, vid)
        return Outcome("skipped", reason="missing_row")
    if status != "ingesting":
        log.info("ingest: video %s is %s, nothing to do", vid, status)
        return Outcome("skipped", reason=status)

    try:
        with tempfile.TemporaryDirectory(prefix="pv-ingest-", dir=deps.tmp_root) as tmp:
            got = ingest.download(url, Path(tmp), deps.ydl_factory, deps.cfg.max_video_duration_s)
            media = deps.ffprobe(got.path)
            if media.duration_ms is not None and media.duration_ms > deps.cfg.max_video_duration_s * 1000:
                raise ingest.IngestError("too_long", f"{media.duration_ms} ms")
            key = source_key(org, vid, "mp4")
            try:
                size = upload_file(deps.s3, deps.cfg.storage_bucket, key, got.path)
            except Exception as e:
                raise ingest.IngestError("storage_failed", repr(e)) from None
    except ingest.IngestError as e:
        return _failed(e, vid, org, deps, failures)
    except Exception as e:  # yt-dlp/ffmpeg/OS surprises: retry like a transient failure
        log.exception("ingest: unexpected error for %s", vid)
        return _failed(ingest.IngestError("download_failed", repr(e)), vid, org, deps, failures)

    updated = repository.mark_ready(
        deps.engine,
        org,
        vid,
        object_key=key,
        size_bytes=size,
        duration_ms=media.duration_ms,
        fps=media.fps or got.probe.fps,
        width=media.width or got.probe.width,
        height=media.height or got.probe.height,
        title=got.probe.title,
        thumbnail_url=got.probe.thumbnail_url,
    )
    if not updated:  # deleted while we were downloading: don't keep an orphan object
        log.info("ingest: video %s changed state during download; removing %s", vid, key)
        deps.s3.delete_object(Bucket=deps.cfg.storage_bucket, Key=key)
        return Outcome("skipped", reason="state_changed")
    metrics.record_ingest("download", "success", "", deps.redis)
    _publish(deps.redis, vid, "ready", None)
    return Outcome("done")


def _failed(e: ingest.IngestError, vid: uuid.UUID, org: uuid.UUID, deps: IngestDeps, failures: int) -> Outcome:
    if e.retryable and failures < len(RETRY_COUNTDOWNS_S):
        log.warning("ingest: %s attempt %d failed (%s), retrying", vid, failures + 1, e)
        metrics.record_ingest("download", "retry", e.reason, deps.redis)
        return Outcome("retry", RETRY_COUNTDOWNS_S[failures], e.reason)
    error = {"code": "INGEST_FAILED", "reason": e.reason, "detail": e.detail or None}
    repository.mark_failed(deps.engine, org, vid, error)
    metrics.record_ingest("download", "failed" if e.retryable else "rejected", e.reason, deps.redis)
    _publish(deps.redis, vid, "failed", error)
    return Outcome("failed", reason=e.reason)


def _publish(redis_client: Any | None, vid: uuid.UUID, status: str, error: dict[str, Any] | None) -> None:
    """Best-effort status event on `video:{id}` for the future WS fan-out (AD §10.3)."""
    if redis_client is None:
        return
    event = {"type": "video.status", "video_id": str(vid), "status": status, "error": error}
    try:
        redis_client.publish(f"video:{vid}", json.dumps(event))
    except Exception as e:
        log.warning("ingest: publish event failed: %r", e)


@lru_cache(maxsize=1)
def _redis() -> Any | None:
    try:
        import redis

        return redis.Redis.from_url(WorkerConfig.from_env().redis_url, socket_connect_timeout=2, socket_timeout=2)
    except Exception as e:
        log.warning("redis client unavailable: %r", e)
        return None


def _s3(cfg: WorkerConfig) -> Any:
    return s3_client(cfg)
