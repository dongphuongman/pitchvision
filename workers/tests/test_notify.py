"""cpu.deliver_notification_emails (M1-BE-19): pending email rows → identity-svc, outcome per row — real Postgres
(pv_worker, RLS); identity is a fake ``post``."""
from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from pitchvision_worker import notify
from pitchvision_worker.config import WorkerConfig
from pitchvision_worker.repository import org_tx
from sqlalchemy import Engine, create_engine, text


@pytest.fixture
def worker_db(pg_worker_url: str) -> Iterator[Engine]:
    e = create_engine(pg_worker_url)
    yield e
    e.dispose()


def _cfg(**over: Any) -> WorkerConfig:
    values: dict[str, Any] = {"identity_internal_url": "http://identity:8001", "internal_token": "tok"} | over
    return dataclasses.replace(WorkerConfig.from_env({}), **values)


def _setup(engine: Engine) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    org, vid, user = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with org_tx(engine, org) as c:
        c.execute(text("INSERT INTO videos (id, org_id, created_by, source_type, status, title) "
                       "VALUES (:v, :o, :u, 'upload', 'ready', 'Derby')"), {"v": vid, "o": org, "u": user})  # fmt: skip
    return org, vid, user


def _status(engine: Engine, org: uuid.UUID) -> dict[str, tuple[str, int]]:
    with org_tx(engine, org) as c:
        rows = c.execute(text("SELECT type, email_status, email_attempts FROM notifications WHERE org_id = :o"),
                         {"o": org})  # fmt: skip
        return {r[0]: (r[1], r[2]) for r in rows}


def test_add_dedupes_and_marks_in_app_only_types(worker_db: Engine) -> None:
    org, vid, user = _setup(worker_db)
    with org_tx(worker_db, org) as c:
        assert notify.add(c, org_id=org, user_id=user, type_="job_ready", video_id=vid, data={}, dedupe_key="k1")
        assert not notify.add(c, org_id=org, user_id=user, type_="job_ready", video_id=vid, data={}, dedupe_key="k1")
        assert not notify.add(c, org_id=org, user_id=None, type_="job_ready", video_id=vid, data={}, dedupe_key="k2")
        assert notify.add(c, org_id=org, user_id=user, type_="quota_80", video_id=None, data={}, dedupe_key="q")
    assert _status(worker_db, org) == {"job_ready": ("pending", 0), "quota_80": ("skipped", 0)}


def test_deliver_emails(worker_db: Engine) -> None:
    org, vid, user = _setup(worker_db)
    with org_tx(worker_db, org) as c:
        for t in ("job_ready", "job_failed", "export_ready", "needs_teams"):
            notify.add(c, org_id=org, user_id=user, type_=t, video_id=vid, data={"filename": "a.mp4"}, dedupe_key=t)
    calls: list[tuple[str, dict[str, Any], dict[str, str]]] = []

    def post(url: str, body: dict[str, Any], headers: dict[str, str]) -> httpx.Response:
        calls.append((url, body, headers))
        if body["org_id"] != str(org):
            return httpx.Response(200, json={"sent": False})
        if body["type"] == "job_failed":
            raise httpx.ConnectError("down")
        if body["type"] == "export_ready":
            return httpx.Response(404, json={})
        if body["type"] == "needs_teams":
            return httpx.Response(200, json={"sent": False, "reason": "unverified"})
        return httpx.Response(200, json={"sent": True})

    notify.deliver_emails(worker_db, _cfg(), post, batch=1000)
    mine = [c for c in calls if c[1]["org_id"] == str(org)]  # the shared test DB may hold other tests' rows
    url, ready, headers = next(c for c in mine if c[1]["type"] == "job_ready")
    assert url == "http://identity:8001/internal/v1/notifications/email"
    assert headers == {"Authorization": "Bearer tok"}
    assert ready == {"user_id": str(user), "org_id": str(org), "type": "job_ready", "video_title": "Derby",
                     "path": f"/videos/{vid}", "data": {"filename": "a.mp4"}}  # fmt: skip
    assert _status(worker_db, org) == {"job_ready": ("sent", 1), "job_failed": ("pending", 1),
                                       "export_ready": ("failed", 1), "needs_teams": ("skipped", 1)}  # fmt: skip
    for _ in range(notify.MAX_EMAIL_ATTEMPTS):
        notify.deliver_emails(worker_db, _cfg(), post, batch=1000)
    assert _status(worker_db, org)["job_failed"] == ("failed", notify.MAX_EMAIL_ATTEMPTS)


def test_without_identity_config_everything_is_in_app_only(worker_db: Engine) -> None:
    org, vid, user = _setup(worker_db)
    with org_tx(worker_db, org) as c:
        notify.add(c, org_id=org, user_id=user, type_="job_ready", video_id=vid, data={}, dedupe_key="x")

    def post(*_a: Any) -> httpx.Response:
        raise AssertionError("must not call identity")

    notify.deliver_emails(worker_db, _cfg(identity_internal_url=None), post, batch=1000)
    assert _status(worker_db, org) == {"job_ready": ("skipped", 1)}
