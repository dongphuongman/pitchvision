"""Worker container entrypoint: one image, role picked by ``WORKER_ROLE`` (AD §5.1).

    WORKER_ROLE=orchestrator|cpu|gpu|ingest|live|runpod python -m pitchvision_worker.entrypoints
    WORKER_ROLE=cpu python -m pitchvision_worker.entrypoints --healthcheck

Celery roles (orchestrator, cpu, gpu, ingest) start a worker bound to exactly one queue.
``live`` (MediaMTX consumer, M2) and ``runpod`` (serverless handler) are reserved
roles: they are accepted names but not implemented yet and exit non-zero.

Optional env:
    WORKER_CONCURRENCY  Celery process count (default: Celery's CPU count; gpu: 1)
    WORKER_LOGLEVEL     Celery log level (default: INFO)
    PV_REDIS_URL        broker/backend URL (default: redis://localhost:6379/0)
    PV_BEAT_SCHEDULE    orchestrator only: beat state file (default /tmp/pv-celerybeat-schedule)
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum

EXIT_OK = 0
EXIT_UNHEALTHY = 1
EXIT_BAD_ROLE = 2
EXIT_NOT_IMPLEMENTED = 3


class Role(StrEnum):
    ORCHESTRATOR = "orchestrator"
    CPU = "cpu"
    GPU = "gpu"
    INGEST = "ingest"
    LIVE = "live"
    RUNPOD = "runpod"


# Role -> the single Celery queue it consumes.
CELERY_ROLE_QUEUES: dict[Role, str] = {
    Role.ORCHESTRATOR: "orchestrate",
    Role.CPU: "cpu",
    Role.GPU: "gpu",
    Role.INGEST: "ingest",
}
# One process per GPU: segment inference owns the device (TensorRT context).
# Ingest: downloads are network-bound and long; 4 slots keep a probe (≤ 20 s budget) from queueing behind them.
DEFAULT_CONCURRENCY: dict[Role, int] = {Role.GPU: 1, Role.INGEST: 4}


class UnknownRoleError(ValueError):
    pass


def parse_role(env: Mapping[str, str]) -> Role:
    raw = env.get("WORKER_ROLE", "").strip().lower()
    if not raw:
        raise UnknownRoleError(f"WORKER_ROLE is not set; expected one of: {', '.join(r.value for r in Role)}")
    try:
        return Role(raw)
    except ValueError:
        raise UnknownRoleError(
            f"unknown WORKER_ROLE={raw!r}; expected one of: {', '.join(r.value for r in Role)}"
        ) from None


def node_name(role: Role, hostname: str | None = None) -> str:
    return f"{role.value}@{hostname or socket.gethostname()}"


def celery_worker_argv(role: Role, env: Mapping[str, str]) -> list[str]:
    queue = CELERY_ROLE_QUEUES[role]
    argv = [
        "worker",
        f"--loglevel={env.get('WORKER_LOGLEVEL', 'INFO')}",
        f"--queues={queue}",
        f"--hostname={role.value}@%h",
    ]
    concurrency = env.get("WORKER_CONCURRENCY") or (
        str(DEFAULT_CONCURRENCY[role]) if role in DEFAULT_CONCURRENCY else None
    )
    if concurrency:
        argv.append(f"--concurrency={int(concurrency)}")
    if role == Role.ORCHESTRATOR:
        # embedded beat: periodic `orchestrate.sweep` (job recovery, AD §16.1). Duplicate sweeps from several
        # orchestrator replicas are harmless — every step locks its job row.
        argv += ["--beat", f"--schedule={env.get('PV_BEAT_SCHEDULE', '/tmp/pv-celerybeat-schedule')}"]
    return argv


def ort_threads_per_process(argv: Sequence[str], env: Mapping[str, str], cpus: int | None = None) -> int | None:
    """Cores ÷ worker processes, so N prefork children don't each start one ONNX Runtime thread per core."""
    if env.get("PITCHVISION_ORT_THREADS"):
        return None  # explicit setting wins
    procs = next((int(a.split("=", 1)[1]) for a in argv if a.startswith("--concurrency=")), None)
    if not procs:
        return None
    return max(1, (cpus or os.cpu_count() or 1) // procs)


def _run_celery(argv: list[str]) -> int:  # pragma: no cover - exercised in the container
    from pitchvision_worker.celery_app import app

    app.worker_main(argv)
    return EXIT_OK


def _celery_ping(role: Role) -> bool:  # pragma: no cover - needs a broker
    from pitchvision_worker.celery_app import app

    replies = app.control.ping(destination=[node_name(role)], timeout=3.0)
    return bool(replies)


def main(
    argv: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
    *,
    run_celery: Callable[[list[str]], int] = _run_celery,
    celery_ping: Callable[[Role], bool] = _celery_ping,
) -> int:
    parser = argparse.ArgumentParser(prog="python -m pitchvision_worker.entrypoints")
    parser.add_argument("--healthcheck", action="store_true", help="exit 0 if this container's worker answers ping")
    args = parser.parse_args(argv)
    env = os.environ if env is None else env

    try:
        role = parse_role(env)
    except UnknownRoleError as exc:
        print(f"pitchvision-worker: {exc}", file=sys.stderr)
        return EXIT_BAD_ROLE

    if role not in CELERY_ROLE_QUEUES:
        print(f"pitchvision-worker: role {role.value!r} is reserved but not implemented yet", file=sys.stderr)
        return EXIT_NOT_IMPLEMENTED

    if args.healthcheck:
        return EXIT_OK if celery_ping(role) else EXIT_UNHEALTHY

    argv_celery = celery_worker_argv(role, env)
    threads = ort_threads_per_process(argv_celery, env)
    if threads and "PITCHVISION_ORT_THREADS" not in env:
        os.environ["PITCHVISION_ORT_THREADS"] = str(threads)  # inherited by the prefork children
    print(f"pitchvision-worker: starting role={role.value} queue={CELERY_ROLE_QUEUES[role]} "
          f"ort_threads={os.environ.get('PITCHVISION_ORT_THREADS', 'default')}", file=sys.stderr)
    return run_celery(argv_celery)


if __name__ == "__main__":
    raise SystemExit(main())
