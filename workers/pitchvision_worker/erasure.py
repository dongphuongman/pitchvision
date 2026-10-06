"""Video erasure (AD §14: deletion reaches R2 + DB + CDN within 24 h). Celery task ``cpu.delete_video_objects``.

The API soft-deletes the videos row and publishes ``cpu.delete_video_objects(org_id, video_id)`` BEFORE its
transaction commits. So the task:

1. waits until the row reads ``deleted`` (a few short retries); a row that stays alive means the request rolled back
   → nothing is deleted. A missing row (never existed / purged) is safe to sweep.
2. aborts every in-progress multipart upload and deletes every object under ``keys.video_prefixes`` (all storage
   classes) — paginated ListObjectsV2 + DeleteObjects batches of ≤ 1000. Idempotent: a re-run deletes nothing new.
3. schedules ONE final pass after ``FINAL_PASS_AFTER_S``: a stage task that was already running when its job was
   cancelled is not killed by revoke and may still upload outputs (longest stage limit: 3 h + grace).

DB rows stay soft-deleted (status ``deleted``); purging them and the CDN cache is the M1 ops erasure job.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from celery import Task
from pitchvision_schemas import keys

from pitchvision_worker import repository
from pitchvision_worker.celery_app import QUEUE_CPU, app
from pitchvision_worker.config import WorkerConfig
from pitchvision_worker.storage import s3_client

log = logging.getLogger(__name__)

TASK_DELETE_VIDEO_OBJECTS = "cpu.delete_video_objects"
ROW_WAIT_S, ROW_WAIT_TRIES = 2, 10  # the API publishes before its transaction commits
FINAL_PASS_AFTER_S = 4 * 3600  # > longest stage time limit (normalize 3 h + 120 s grace); < visibility timeout 6 h
DELETE_BATCH = 1000  # S3/R2 DeleteObjects limit


@dataclass(frozen=True)
class Erased:
    objects: int = 0
    uploads: int = 0


def erase_prefixes(client: Any, bucket: str, prefixes: list[str], *, page_size: int = DELETE_BATCH) -> Erased:
    """Abort multipart uploads and delete every object under each prefix. Raises if storage reports a failure."""
    objects = uploads = 0
    for prefix in prefixes:
        uploads += _abort_uploads(client, bucket, prefix, page_size)
        token: str | None = None
        while True:
            kwargs: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": page_size}
            if token:
                kwargs["ContinuationToken"] = token
            page = client.list_objects_v2(**kwargs)
            batch = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            for i in range(0, len(batch), DELETE_BATCH):
                chunk = batch[i : i + DELETE_BATCH]
                resp = client.delete_objects(Bucket=bucket, Delete={"Objects": chunk, "Quiet": True})
                errors = [e for e in resp.get("Errors", []) if e.get("Code") != "NoSuchKey"]
                if errors:
                    raise RuntimeError(f"delete_objects under {prefix}: {errors[:3]}")
                objects += len(chunk)
            if not page.get("IsTruncated"):
                break
            token = page.get("NextContinuationToken")
    return Erased(objects, uploads)


def _abort_uploads(client: Any, bucket: str, prefix: str, page_size: int) -> int:
    n = 0
    markers: dict[str, str] = {}
    while True:
        page = client.list_multipart_uploads(Bucket=bucket, Prefix=prefix, MaxUploads=page_size, **markers)
        for u in page.get("Uploads", []):
            try:
                client.abort_multipart_upload(Bucket=bucket, Key=u["Key"], UploadId=u["UploadId"])
                n += 1
            except client.exceptions.NoSuchUpload:
                pass
        if not page.get("IsTruncated"):
            return n
        markers = {"KeyMarker": page["NextKeyMarker"], "UploadIdMarker": page["NextUploadIdMarker"]}


@dataclass(frozen=True)
class Outcome:
    kind: str  # erased | wait_row | skipped
    erased: Erased = Erased()


@dataclass
class ErasureDeps:
    cfg: WorkerConfig
    engine: Any
    s3: Any
    page_size: int = DELETE_BATCH

    @classmethod
    def from_env(cls) -> ErasureDeps:
        cfg = WorkerConfig.from_env()
        return cls(cfg, repository.engine_for(cfg.database_url), s3_client(cfg))


def run_delete_video_objects(org_id: str, video_id: str, deps: ErasureDeps, *, row_waits: int = 0) -> Outcome:
    org, vid = uuid.UUID(org_id), uuid.UUID(video_id)
    status = repository.get_status(deps.engine, org, vid)
    if status not in (None, "deleted"):
        if row_waits < ROW_WAIT_TRIES:
            return Outcome("wait_row")
        log.warning("erasure: video %s is still %s; the delete request rolled back — nothing erased", vid, status)
        return Outcome("skipped")
    erased = erase_prefixes(deps.s3, deps.cfg.storage_bucket, keys.video_prefixes(org_id, video_id),
                            page_size=deps.page_size)  # fmt: skip
    log.info("erasure: video %s: %d object(s) deleted, %d upload(s) aborted", vid, erased.objects, erased.uploads)
    return Outcome("erased", erased)


Scheduler = Callable[[dict[str, Any], float], None]


def _schedule(kwargs: dict[str, Any], countdown: float) -> None:
    app.send_task(TASK_DELETE_VIDEO_OBJECTS, kwargs=kwargs, queue=QUEUE_CPU, countdown=countdown)


@app.task(name=TASK_DELETE_VIDEO_OBJECTS, queue=QUEUE_CPU, bind=True, max_retries=ROW_WAIT_TRIES + 5,
          autoretry_for=(Exception,), retry_backoff=30, retry_backoff_max=600)  # fmt: skip
def delete_video_objects(self: Task, org_id: str, video_id: str, final: bool = False, row_waits: int = 0) -> str:
    outcome = run_delete_video_objects(org_id, video_id, ErasureDeps.from_env(), row_waits=row_waits)
    if outcome.kind == "wait_row":
        kwargs = {"org_id": org_id, "video_id": video_id, "final": final, "row_waits": row_waits + 1}
        raise self.retry(kwargs=kwargs, countdown=ROW_WAIT_S)
    if outcome.kind == "erased" and not final:
        after_erasure(org_id, video_id)
    return outcome.kind


def after_erasure(org_id: str, video_id: str, schedule: Scheduler = _schedule) -> None:
    """One delayed final pass catches outputs of stage tasks that were mid-flight when the job was cancelled."""
    schedule({"org_id": org_id, "video_id": video_id, "final": True}, FINAL_PASS_AFTER_S)
