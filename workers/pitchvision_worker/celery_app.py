"""Celery app factory for the worker image (AD §4 containers, §5.1).

One image, three Celery roles, one queue each:

- ``orchestrate`` — orchestrator: builds the job DAG, fans out segment tasks.
- ``cpu``         — cpu-worker: ffmpeg/ffprobe, stitching, stats, exports, LLM calls.
- ``gpu``         — gpu-worker: segment inference (TensorRT).
- ``ingest``      — ingest-worker: YouTube probe/download (S1). Own queue so it can run on a host with a
                    different egress IP (M1-OPS-06, SP-10: datacenter IPs untested against bot-checks).

Task names (the contract — the API publishes by name only):
- ``orchestrate.*``   orchestrator (pitchvision_worker.orchestrator.tasks)
- ``cpu.run_stage``   CpuTask executor (S2 normalise)
- ``cpu.delete_video_objects``  video erasure: every object under keys.video_prefixes (pitchvision_worker.erasure)
- ``gpu.run_stage``   GpuTask executor, queue ``PV_LOCAL_GPU_QUEUE`` (pitchvision_worker.compute.tasks)
- ``ingest.*``        S1 (pitchvision_worker.tasks)
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from celery import Celery
from kombu import Queue

QUEUE_ORCHESTRATE = "orchestrate"
QUEUE_CPU = "cpu"
QUEUE_GPU = "gpu"
QUEUE_INGEST = "ingest"
QUEUES: tuple[str, ...] = (QUEUE_ORCHESTRATE, QUEUE_CPU, QUEUE_GPU, QUEUE_INGEST)

# Redis redelivers an unacked (acks_late) task after this long; must exceed the longest task (3 h match
# download on a slow egress), or a second worker starts the same download.
VISIBILITY_TIMEOUT_S = 6 * 3600

TASK_START_JOB = "orchestrate.start_job"
TASK_ADVANCE = "orchestrate.advance"
TASK_CANCEL_JOB = "orchestrate.cancel_job"
TASK_ON_RESULT = "orchestrate.on_task_result"
TASK_VIDEO_READY = "orchestrate.video_ready"
TASK_SWEEP = "orchestrate.sweep"

DEFAULT_REDIS_URL = "redis://localhost:6379/0"


def redis_url(env: Mapping[str, str] | None = None) -> str:
    """Broker/result URL. Same ``PV_`` prefix as the API settings."""
    env = os.environ if env is None else env
    return env.get("PV_REDIS_URL") or DEFAULT_REDIS_URL


def _sweep_interval_s() -> float:
    return float(os.environ.get("PV_SWEEP_INTERVAL_S") or 30)


def create_app(broker_url: str | None = None) -> Celery:
    url = broker_url or redis_url()
    include = ["pitchvision_worker.tasks", "pitchvision_worker.compute.tasks", "pitchvision_worker.orchestrator.tasks",
               "pitchvision_worker.erasure"]
    app = Celery("pitchvision_worker", broker=url, backend=url, include=include)
    app.conf.update(
        task_queues=[Queue(name) for name in QUEUES],
        task_default_queue=QUEUE_CPU,
        task_routes={"ingest.*": {"queue": QUEUE_INGEST}, "orchestrate.*": {"queue": QUEUE_ORCHESTRATE},
                     "cpu.*": {"queue": QUEUE_CPU}},  # gpu.run_stage: queue chosen per call (PV_LOCAL_GPU_QUEUE)
        # the orchestrator's restart/periodic recovery (run by the orchestrator worker's embedded beat)
        beat_schedule={"orchestrate-sweep": {"task": TASK_SWEEP, "schedule": _sweep_interval_s(),
                                             "options": {"queue": QUEUE_ORCHESTRATE, "expires": _sweep_interval_s()}}},
        task_track_started=True,  # LocalBackend.status() distinguishes queued from running
        broker_transport_options={"visibility_timeout": VISIBILITY_TIMEOUT_S},
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
