"""Celery app factory for the worker image (AD §4 containers, §5.1).

One image, three Celery roles, one queue each:

- ``orchestrate`` — orchestrator: builds the job DAG, fans out segment tasks.
- ``cpu``         — cpu-worker: ffmpeg/ffprobe, stitching, stats, exports, LLM calls.
- ``gpu``         — gpu-worker: segment inference (TensorRT).

W1 skeleton: only the queues and a ``ping`` task exist so containers start and can be
health-checked. Pipeline tasks land with the M1-BE/M1-CV PRs.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from celery import Celery
from kombu import Queue

QUEUE_ORCHESTRATE = "orchestrate"
QUEUE_CPU = "cpu"
QUEUE_GPU = "gpu"
QUEUES: tuple[str, ...] = (QUEUE_ORCHESTRATE, QUEUE_CPU, QUEUE_GPU)

DEFAULT_REDIS_URL = "redis://localhost:6379/0"


def redis_url(env: Mapping[str, str] | None = None) -> str:
    """Broker/result URL. Same ``PV_`` prefix as the API settings."""
    env = os.environ if env is None else env
    return env.get("PV_REDIS_URL") or DEFAULT_REDIS_URL


def create_app(broker_url: str | None = None) -> Celery:
    url = broker_url or redis_url()
    app = Celery("pitchvision_worker", broker=url, backend=url, include=["pitchvision_worker.tasks"])
    app.conf.update(
        task_queues=[Queue(name) for name in QUEUES],
        task_default_queue=QUEUE_CPU,
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        # Long-running segment tasks: ack after completion, one task per process at a time.
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        result_expires=3600,
        broker_connection_retry_on_startup=True,
        worker_hijack_root_logger=False,
    )
    return app


app = create_app()
