"""Ingest counters (M1-OPS-06: YouTube download success rate) until Prometheus lands in OPS-08.

Each outcome is (1) logged as a structured line `metric=ingest_total outcome=… reason=…`, (2) counted in
process, and (3) HINCRBY'd into the Redis hash `pv:metrics:ingest_total` (field `<outcome>:<reason>`) so the
rate is visible across processes/hosts:  `redis-cli HGETALL pv:metrics:ingest_total`.
Recording never raises — metrics must not fail a download.
"""

from __future__ import annotations

import logging
import threading
from collections import Counter
from typing import Any

log = logging.getLogger("pitchvision_worker.metrics")

REDIS_KEY = "pv:metrics:ingest_total"

_lock = threading.Lock()
_counts: Counter[tuple[str, str, str]] = Counter()


def record_ingest(op: str, outcome: str, reason: str = "", redis_client: Any | None = None) -> None:
    """op: probe|download · outcome: success|rejected|failed|retry · reason: IngestError.reason."""
    with _lock:
        _counts[(op, outcome, reason)] += 1
    log.info("metric=ingest_total op=%s outcome=%s reason=%s", op, outcome, reason or "-")
    if redis_client is not None:
        try:
            redis_client.hincrby(REDIS_KEY, f"{op}:{outcome}:{reason or '-'}", 1)
        except Exception as e:
            log.warning("metrics: redis HINCRBY failed: %r", e)


def snapshot() -> dict[tuple[str, str, str], int]:
    with _lock:
        return dict(_counts)


def reset() -> None:
    with _lock:
        _counts.clear()
