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
- ``cpu.build_data_export`` / ``cpu.privacy_sweep``  SCR-40 data export zip; expired archives + org / account
  erasure pulled from identity-svc (pitchvision_worker.privacy)
- ``cpu.extract_frame``  still JPEG of normalized.mp4 for SCR-14/15, cached under media/…/frames/ (frames.py)
- ``gpu.run_stage``   GpuTask executor, queue ``PV_LOCAL_GPU_QUEUE`` (pitchvision_worker.compute.tasks)
- ``ingest.*``        S1 (pitchvision_worker.tasks)
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from celery import Celery, signals
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
TASK_SWEEP_EXPORTS = "cpu.sweep_exports"
TASK_DELIVER_EMAILS = "cpu.deliver_notification_emails"
TASK_PRIVACY_SWEEP = "cpu.privacy_sweep"

DEFAULT_REDIS_URL = "redis://localhost:6379/0"


def redis_url(env: Mapping[str, str] | None = None) -> str:
    """Broker/result URL. Same ``PV_`` prefix as the API settings."""
    env = os.environ if env is None else env
    return env.get("PV_REDIS_URL") or DEFAULT_REDIS_URL


def _sweep_interval_s() -> float:
    return float(os.environ.get("PV_SWEEP_INTERVAL_S") or 30)


def _export_sweep_s() -> float:
    return float(os.environ.get("PV_EXPORT_SWEEP_S") or 600)


def _notify_email_s() -> float:
    return float(os.environ.get("PV_NOTIFY_EMAIL_S") or 60)


def _privacy_sweep_s() -> float:
    return float(os.environ.get("PV_PRIVACY_SWEEP_S") or 600)


def create_app(broker_url: str | None = None) -> Celery:
    url = broker_url or redis_url()
    include = ["pitchvision_worker.tasks", "pitchvision_worker.compute.tasks", "pitchvision_worker.orchestrator.tasks",
               "pitchvision_worker.erasure", "pitchvision_worker.frames", "pitchvision_worker.exports",
               "pitchvision_worker.notify", "pitchvision_worker.privacy"]
    app = Celery("pitchvision_worker", broker=url, backend=url, include=include)
    app.conf.update(
        task_queues=[Queue(name) for name in QUEUES],
        task_default_queue=QUEUE_CPU,
        task_routes={"ingest.*": {"queue": QUEUE_INGEST}, "orchestrate.*": {"queue": QUEUE_ORCHESTRATE},
                     "cpu.*": {"queue": QUEUE_CPU}},  # gpu.run_stage: queue chosen per call (PV_LOCAL_GPU_QUEUE)
        # the orchestrator's restart/periodic recovery (run by the orchestrator worker's embedded beat)
        beat_schedule={"orchestrate-sweep": {"task": TASK_SWEEP, "schedule": _sweep_interval_s(),
                                             "options": {"queue": QUEUE_ORCHESTRATE, "expires": _sweep_interval_s()}},
                       # exports: fail renders whose worker died, delete expired / deleted files
                       "exports-sweep": {"task": TASK_SWEEP_EXPORTS, "schedule": _export_sweep_s(),
                                         "options": {"queue": QUEUE_CPU, "expires": _export_sweep_s()}},
                       # notifications: hand pending emails to identity-svc
                       "notification-emails": {"task": TASK_DELIVER_EMAILS, "schedule": _notify_email_s(),
                                               "options": {"queue": QUEUE_CPU, "expires": _notify_email_s()}},
                       # SCR-40 data & privacy: expired data exports, org / account erasure (AD §14, 24 h)
                       "privacy-sweep": {"task": TASK_PRIVACY_SWEEP, "schedule": _privacy_sweep_s(),
                                         "options": {"queue": QUEUE_CPU, "expires": _privacy_sweep_s()}}},
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


def init_sentry(cfg: Any) -> bool:
    """Errors to Sentry when PV_SENTRY_DSN is set (AD §17): release = image tag, tags service = worker role."""
    if not cfg.sentry_dsn:
        return False
    import sentry_sdk

    sentry_sdk.init(dsn=cfg.sentry_dsn, environment=cfg.env, release=cfg.image_tag, send_default_pii=False)
    sentry_sdk.set_tag("service", f"worker-{os.environ.get('WORKER_ROLE', 'cpu')}")
    sentry_sdk.set_tag("region", cfg.region)
    return True


@signals.celeryd_init.connect
def _sentry_on_start(**_: Any) -> None:
    from pitchvision_worker.config import WorkerConfig

    init_sentry(WorkerConfig.from_env())


app = create_app()
