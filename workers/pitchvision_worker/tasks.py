"""Worker tasks. W1: only ``ping`` (smoke test for broker + routing)."""

from __future__ import annotations

from pitchvision_worker.celery_app import app


@app.task(name="pitchvision_worker.ping")
def ping() -> str:
    return "pong"
