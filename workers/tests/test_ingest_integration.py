"""Integration (Docker): worker repository against the real schema + RLS, and the API↔worker Celery contract."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from pitchvision_worker import repository
from pitchvision_worker.stages import ingest
from sqlalchemy import Engine, create_engine


@pytest.fixture
def engine(pg_app_url: str) -> Iterator[Engine]:
    eng = create_engine(pg_app_url)
    yield eng
    eng.dispose()


def _insert_video(pg_app_url: str, org: uuid.UUID, status: str = "ingesting", title: str | None = None) -> uuid.UUID:
    dsn = pg_app_url.replace("postgresql+psycopg://", "postgresql://")
    vid = uuid.uuid4()
    with psycopg.connect(dsn) as conn:
        conn.execute("SELECT set_config('app.org_id', %s, true)", (str(org),))
        conn.execute(
            "INSERT INTO videos (id, org_id, created_by, source_type, source_uri, title, status) "
            "VALUES (%s, %s, %s, 'youtube', 'https://www.youtube.com/watch?v=x', %s, %s)",
            (vid, org, uuid.uuid4(), title, status),
        )
    return vid


def _row(pg_app_url: str, org: uuid.UUID, vid: uuid.UUID) -> dict[str, Any] | None:
    from psycopg.rows import dict_row

    dsn = pg_app_url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SELECT set_config('app.org_id', %s, true)", (str(org),))
        return conn.execute("SELECT * FROM videos WHERE id = %s", (vid,)).fetchone()


def test_mark_ready_under_rls(pg_app_url: str, engine: Engine) -> None:
    assert "//pv_app:" in pg_app_url  # connected as pv_app (no BYPASSRLS)
    org, other = uuid.uuid4(), uuid.uuid4()
    vid = _insert_video(pg_app_url, org)
    assert repository.get_status(engine, org, vid) == "ingesting"
    assert repository.get_status(engine, other, vid) is None  # RLS: wrong org sees nothing
    common = dict(object_key="k", size_bytes=10, duration_ms=1000, fps=25.0, width=1920, height=1080,
                  title="From YouTube", thumbnail_url="https://i.ytimg.com/x.jpg")  # fmt: skip
    assert repository.mark_ready(engine, other, vid, **common) is False  # type: ignore[arg-type]
    assert repository.mark_ready(engine, org, vid, **common) is True  # type: ignore[arg-type]
    row = _row(pg_app_url, org, vid)
    assert row is not None
    assert (row["status"], row["object_key"], row["size_bytes"], row["title"]) == ("ready", "k", 10, "From YouTube")
    assert float(row["fps"]) == 25.0 and row["error"] is None
    # redelivered task / second writer: guarded on status = 'ingesting'
    assert repository.mark_failed(engine, org, vid, {"code": "INGEST_FAILED", "reason": "x"}) is False


def test_user_title_kept_and_failure_recorded(pg_app_url: str, engine: Engine) -> None:
    org = uuid.uuid4()
    vid = _insert_video(pg_app_url, org, title="My title")
    assert repository.mark_failed(engine, org, vid, {"code": "INGEST_FAILED", "reason": "blocked", "detail": "d"})
    row = _row(pg_app_url, org, vid)
    assert row is not None and row["status"] == "failed" and row["error"]["reason"] == "blocked"
    vid2 = _insert_video(pg_app_url, org, title="My title")
    repository.mark_ready(engine, org, vid2, object_key="k", size_bytes=1, duration_ms=None, fps=None, width=None,
                          height=None, title="YT title", thumbnail_url=None)  # fmt: skip
    row2 = _row(pg_app_url, org, vid2)
    assert row2 is not None and row2["title"] == "My title"


def test_api_probe_round_trip_over_redis(redis_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """The API's publish-only client reaches the worker's task by name on queue `ingest` and gets the result."""
    import asyncio
    import gc

    from celery.contrib.testing.worker import start_worker
    from pitchvision_api.services.tasks import QUEUE_INGEST, TASK_PROBE_URL, CeleryTaskQueue
    from pitchvision_worker import tasks
    from pitchvision_worker.celery_app import create_app

    worker_app = create_app(redis_url)
    worker_app.loader.import_default_modules()
    # the task objects were bound to the module-level app; register the probe on this test app by name
    worker_app.task(name=TASK_PROBE_URL, queue=QUEUE_INGEST)(tasks.probe_url.run)
    seen: list[str] = []

    def fake_probe(url: str, factory: Any = None) -> ingest.ProbeInfo:
        seen.append(url)
        return ingest.ProbeInfo("T", 60.0, 1280, 720, 25.0, False, "not_live", "public", 0, None)

    monkeypatch.setattr(ingest, "probe", fake_probe)
    monkeypatch.setattr(tasks, "_redis", lambda: None)
    with start_worker(worker_app, queues=[QUEUE_INGEST], pool="solo", perform_ping_check=False, loglevel="WARNING"):
        api = CeleryTaskQueue(redis_url)
        result = asyncio.run(api.call(TASK_PROBE_URL, {"url": "https://youtu.be/dQw4w9WgXcQ"}, QUEUE_INGEST, 15))
        gc.collect()  # AsyncResult.__del__ talks to Redis: let it run while Redis is still up
    assert result == {
        "ok": True,
        "info": {"title": "T", "duration_s": 60.0, "width": 1280, "height": 720, "fps": 25.0, "is_live": False,
                 "live_status": "not_live", "availability": "public", "age_limit": 0, "thumbnail_url": None},
    }  # fmt: skip
    assert seen == ["https://youtu.be/dQw4w9WgXcQ"]
