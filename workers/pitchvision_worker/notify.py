"""Notifications from the pipeline (SCR-51, M1-BE-19; docs/contracts/pipeline-stages.md "Notifications").

``add`` inserts a row inside the caller's RLS-scoped transaction (the orchestrator's job step, the export task), so
a notification exists exactly when the state change it reports committed. ``(user_id, dedupe_key)`` is unique:
a redelivered step inserts nothing new.

``cpu.deliver_notification_emails`` (beat, every ``PV_NOTIFY_EMAIL_S``, default 60 s) hands ``pending`` rows of an
email type to identity-svc (``POST {PV_IDENTITY_INTERNAL_URL}/internal/v1/notifications/email``, bearer
``PV_INTERNAL_TOKEN``) — identity owns the addresses and the mailer. Without that config the rows become
``skipped`` (in-app only). Transient errors are retried on the next run, ``failed`` after ``MAX_EMAIL_ATTEMPTS``.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx
from pitchvision_schemas import EMAIL_TYPES, notification_path
from sqlalchemy import Connection, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from pitchvision_worker import repository
from pitchvision_worker.celery_app import QUEUE_CPU, TASK_DELIVER_EMAILS, app
from pitchvision_worker.config import WorkerConfig
from pitchvision_worker.repository import notifications, org_tx, teams, videos

log = logging.getLogger(__name__)

MAX_EMAIL_ATTEMPTS = 5
EMAIL_BATCH = 50
HTTP_TIMEOUT_S = 10.0


def add(conn: Connection, *, org_id: uuid.UUID, user_id: uuid.UUID | None, type_: str, video_id: uuid.UUID | None,
        data: Mapping[str, Any], dedupe_key: str) -> bool:  # fmt: skip
    """Insert one notification (no-op without a recipient or when the dedupe key exists). → inserted?"""
    if user_id is None:
        return False
    stmt = pg_insert(notifications).values(
        org_id=org_id, user_id=user_id, type=type_, video_id=video_id, data=dict(data), dedupe_key=dedupe_key,
        email_status="pending" if type_ in EMAIL_TYPES else "skipped", email_attempts=0,
    ).on_conflict_do_nothing(constraint="uq_notifications_dedupe").returning(notifications.c.id)
    return conn.execute(stmt).first() is not None


def teams_confirmed(conn: Connection, video_id: uuid.UUID) -> bool:
    stmt = select(teams.c.confirmed).where(teams.c.video_id == video_id, teams.c.cluster.is_not(None))
    flags = list(conn.execute(stmt).scalars())
    return bool(flags) and all(flags)


# ---------------------------------------------------------------- email delivery
Post = Callable[[str, dict[str, Any], dict[str, str]], httpx.Response]


def _post(url: str, body: dict[str, Any], headers: dict[str, str]) -> httpx.Response:
    return httpx.post(url, json=body, headers=headers, timeout=HTTP_TIMEOUT_S)


@dataclass(frozen=True)
class Delivered:
    sent: int = 0
    skipped: int = 0
    failed: int = 0
    retry: int = 0


def deliver_emails(engine: Any, cfg: WorkerConfig, post: Post = _post, *, batch: int = EMAIL_BATCH) -> Delivered:
    with engine.begin() as conn:
        pending = [(r[0], r[1]) for r in conn.execute(
            text("SELECT notification_id, org_id FROM pv_pending_notification_emails(:n)"), {"n": batch})]
    counts = {"sent": 0, "skipped": 0, "failed": 0, "retry": 0}
    for nid, org in pending:
        with org_tx(engine, org) as conn:
            row = conn.execute(
                select(notifications, videos.c.title.label("video_title"))
                .outerjoin(videos, videos.c.id == notifications.c.video_id)
                .where(notifications.c.id == nid, notifications.c.email_status == "pending")
                .with_for_update(of=notifications, skip_locked=True)).mappings().first()
            if row is None:
                continue
            outcome = _deliver_one(dict(row), cfg, post)
            values: dict[str, Any] = {"email_attempts": notifications.c.email_attempts + 1}
            if outcome == "retry" and row["email_attempts"] + 1 >= MAX_EMAIL_ATTEMPTS:
                outcome = "failed"
            if outcome != "retry":
                values["email_status"] = outcome
            conn.execute(update(notifications).where(notifications.c.id == nid).values(**values))
            counts[outcome] += 1
    if any(counts.values()):
        log.info("notification emails: %s", counts)
    return Delivered(**counts)


def _deliver_one(row: Mapping[str, Any], cfg: WorkerConfig, post: Post) -> str:
    """→ sent | skipped | failed | retry"""
    if not cfg.identity_internal_url or not cfg.internal_token:
        return "skipped"
    vid = str(row["video_id"]) if row["video_id"] else None
    body = {"user_id": str(row["user_id"]), "org_id": str(row["org_id"]), "type": row["type"],
            "video_title": row["video_title"], "path": notification_path(row["type"], vid), "data": row["data"]}
    try:
        r = post(f"{cfg.identity_internal_url}/internal/v1/notifications/email", body,
                 {"Authorization": f"Bearer {cfg.internal_token}"})
    except httpx.HTTPError as e:
        log.warning("notification %s: identity unreachable: %r", row["id"], e)
        return "retry"
    if r.status_code == 200:
        return "sent" if (r.json() or {}).get("sent") else "skipped"
    if r.status_code in (400, 404, 422):  # unknown user / bad request: retrying won't help
        log.warning("notification %s: identity refused (%d): %s", row["id"], r.status_code, r.text[:200])
        return "failed"
    return "retry"


@app.task(name=TASK_DELIVER_EMAILS, queue=QUEUE_CPU, acks_late=False)
def deliver_notification_emails() -> dict[str, int]:
    cfg = WorkerConfig.from_env()
    d = deliver_emails(repository.engine_for(cfg.database_url), cfg)
    return {"sent": d.sent, "skipped": d.skipped, "failed": d.failed, "retry": d.retry}
