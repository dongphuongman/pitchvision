"""Talking to workers by Celery task NAME only (the API never imports pitchvision_worker — import-linter).

Task contract (implemented in workers/pitchvision_worker/tasks.py):
- `ingest.probe_url`  queue `ingest`, kwargs {url}  → {"ok": true, "info": {...}} | {"ok": false, "reason", "detail"}
- `ingest.download`   queue `ingest`, kwargs {video_id, org_id, url} → worker updates the videos row itself
- `orchestrate.start_job`  queue `orchestrate`, kwargs {job_id, org_id} → drives the job DAG (also used by retry)
- `orchestrate.cancel_job` queue `orchestrate`, kwargs {job_id, org_id} → revokes the job's dispatched tasks and
                           publishes the WS `cancelled` event
- `cpu.delete_video_objects` queue `cpu`, kwargs {org_id, video_id} → deletes every object under
                           pitchvision_schemas.keys.video_prefixes once the videos row is `deleted` (AD §14 erasure)
- `cpu.extract_frame`      queue `cpu`, kwargs {org_id, video_id, t_ms} → {"ok": true, "key", "t_ms"} |
                           {"ok": false, "reason": no_video | out_of_range | ffmpeg}; JPEG at keys.frame_key (cached)
- `cpu.render_export`      queue `cpu`, kwargs {export_id, org_id} → S11: renders exports.spec, uploads to
                           keys.export_key and updates the exports row (status, progress, object_key, expires_at)
"""

import asyncio
import contextlib
import logging
from typing import Any, Protocol

from celery import Celery
from celery.exceptions import TimeoutError as CeleryTimeout

log = logging.getLogger(__name__)

QUEUE_INGEST = "ingest"
TASK_PROBE_URL = "ingest.probe_url"
TASK_DOWNLOAD = "ingest.download"
QUEUE_ORCHESTRATE = "orchestrate"
TASK_START_JOB = "orchestrate.start_job"
TASK_CANCEL_JOB = "orchestrate.cancel_job"
QUEUE_CPU = "cpu"
TASK_DELETE_VIDEO_OBJECTS = "cpu.delete_video_objects"
TASK_EXTRACT_FRAME = "cpu.extract_frame"
TASK_RENDER_EXPORT = "cpu.render_export"


class TaskUnavailable(Exception):
    """Broker unreachable / task could not be published."""


class TaskTimeout(Exception):
    """No result within the deadline."""


class TaskFailed(Exception):
    """The task raised instead of returning a result."""


class TaskQueue(Protocol):
    async def send(self, name: str, kwargs: dict[str, Any], queue: str) -> str: ...

    async def call(self, name: str, kwargs: dict[str, Any], queue: str, timeout_s: float) -> Any: ...


class CeleryTaskQueue:
    """Publish-only Celery client on the shared Redis broker; results come back via the Redis backend."""

    def __init__(self, redis_url: str) -> None:
        self.app = Celery("pitchvision_api", broker=redis_url, backend=redis_url)
        self.app.conf.update(
            task_serializer="json",
            result_serializer="json",
            accept_content=["json"],
            # fail fast when Redis is down (defaults retry for ~20 s while the HTTP request hangs)
            broker_connection_timeout=3,
            broker_connection_retry_on_startup=False,
            broker_transport_options={"max_retries": 1, "socket_connect_timeout": 2},
            redis_socket_connect_timeout=2,
            result_backend_transport_options={"retry_policy": {"timeout": 2.0}},
            task_publish_retry_policy={"max_retries": 1, "interval_start": 0, "interval_step": 0.5},
            result_expires=600,
        )

    def _publish(self, name: str, kwargs: dict[str, Any], queue: str, expires: float | None) -> Any:
        try:
            return self.app.send_task(name, kwargs=kwargs, queue=queue, expires=expires)
        except Exception as e:  # kombu OperationalError & friends
            log.warning("publish %s failed: %r", name, e)
            raise TaskUnavailable(str(e)) from None

    async def send(self, name: str, kwargs: dict[str, Any], queue: str) -> str:
        result = await asyncio.to_thread(self._publish, name, kwargs, queue, None)
        return str(result.id)

    async def call(self, name: str, kwargs: dict[str, Any], queue: str, timeout_s: float) -> Any:
        def run() -> Any:
            # expires: a worker that picks it up after the caller gave up drops it instead of probing
            result = self._publish(name, kwargs, queue, timeout_s)
            try:
                return result.get(timeout=timeout_s, propagate=True, disable_sync_subtasks=False)
            except CeleryTimeout:
                raise TaskTimeout(name) from None
            except (TaskUnavailable, TaskTimeout):
                raise
            except Exception as e:
                raise TaskFailed(f"{type(e).__name__}: {e}") from None
            finally:
                with contextlib.suppress(Exception):  # best effort: results expire anyway (result_expires)
                    result.forget()

        return await asyncio.to_thread(run)
