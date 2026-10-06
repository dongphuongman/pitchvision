"""Orchestrator persistence: jobs / job_tasks / shots / videos through the RLS-bound connection (repository.org_tx).

Every function takes an open ``Connection`` whose transaction already ran ``set_config('app.org_id', …)``; the
orchestrator holds ``SELECT … FOR UPDATE`` on the job row for the whole step, which serialises concurrent
``advance`` runs (event, retry timer and sweep may fire together) so a task is never dispatched twice.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from pitchvision_schemas import ShotTimeline
from sqlalchemy import Connection, delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from pitchvision_worker.repository import job_tasks, jobs, shots, videos

Row = dict[str, Any]
ACTIVE = ("queued", "ingesting", "calibrating", "inferring", "postprocessing")


def lock_job(conn: Connection, job_id: uuid.UUID) -> Row | None:
    row = conn.execute(select(jobs).where(jobs.c.id == job_id).with_for_update()).mappings().first()
    return dict(row) if row else None


def get_job(conn: Connection, job_id: uuid.UUID) -> Row | None:
    """Read a job without locking it (a derived job reading its parent)."""
    row = conn.execute(select(jobs).where(jobs.c.id == job_id)).mappings().first()
    return dict(row) if row else None


def update_job(conn: Connection, job_id: uuid.UUID, **values: Any) -> None:
    if "progress" in values:
        values["progress"] = Decimal(str(round(float(values["progress"]), 2)))
    conn.execute(update(jobs).where(jobs.c.id == job_id).values(**values))


def load_tasks(conn: Connection, job_id: uuid.UUID) -> dict[str, Row]:
    rows = conn.execute(select(job_tasks).where(job_tasks.c.job_id == job_id)).mappings()
    return {r["task_key"]: dict(r) for r in rows}


def insert_task(conn: Connection, job_id: uuid.UUID, org_id: uuid.UUID, key: str) -> Row:
    stmt = pg_insert(job_tasks).values(job_id=job_id, org_id=org_id, task_key=key, status="queued", attempts=0)
    conn.execute(stmt.on_conflict_do_nothing(index_elements=["job_id", "task_key"]))
    row = conn.execute(select(job_tasks).where(job_tasks.c.job_id == job_id, job_tasks.c.task_key == key)).mappings()
    return dict(row.one())


def update_task(conn: Connection, row: Row, **values: Any) -> Row:
    values["updated_at"] = func.now()
    stmt = (
        update(job_tasks)
        .where(job_tasks.c.job_id == row["job_id"], job_tasks.c.task_key == row["task_key"])
        .values(**values)
        .returning(*job_tasks.c)
    )
    new = dict(conn.execute(stmt).mappings().one())
    row.clear()
    row.update(new)
    return row


def get_video(conn: Connection, video_id: uuid.UUID) -> Row | None:
    row = conn.execute(select(videos).where(videos.c.id == video_id)).mappings().first()
    return dict(row) if row else None


def update_video_probe(conn: Connection, video_id: uuid.UUID, probe: dict[str, Any]) -> None:
    """S2 probe of the original → videos.fps/width/height/duration_ms (contract: 'probe fields into videos')."""
    values: dict[str, Any] = {k: probe[k] for k in ("width", "height", "duration_ms") if probe.get(k) is not None}
    if probe.get("fps") is not None:
        values["fps"] = Decimal(str(round(float(probe["fps"]), 3)))
    if values:
        conn.execute(update(videos).where(videos.c.id == video_id).values(**values))


def replace_shots(conn: Connection, org_id: uuid.UUID, video_id: uuid.UUID, timeline: ShotTimeline) -> int:
    """S2b rows for the video (idempotent: a re-run replaces the previous timeline)."""
    conn.execute(delete(shots).where(shots.c.video_id == video_id))
    rows = [{"video_id": video_id, "org_id": org_id, "t0_ms": s.t0_ms, "t1_ms": s.t1_ms, "type": s.type.value,
             "conf": float(s.conf)} for s in timeline.shots]  # fmt: skip
    if rows:
        conn.execute(insert(shots), rows)
    return len(rows)


def active_jobs_for_video(conn: Connection, video_id: uuid.UUID) -> Sequence[uuid.UUID]:
    stmt = select(jobs.c.id).where(jobs.c.video_id == video_id, jobs.c.status.in_(ACTIVE))
    return [r[0] for r in conn.execute(stmt)]


def db_now(conn: Connection) -> datetime:
    now: datetime = conn.execute(select(func.now())).scalar_one()
    return now
