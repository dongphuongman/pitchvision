"""Tiny repository for workers (SQLAlchemy Core, sync psycopg 3): videos (ingest), jobs (orchestrator), results.

Design (M1-BE-08): the ingest worker writes the outcome straight to Postgres instead of bouncing it
through the API. Workers connect as `pv_worker` (PV_WORKER_DATABASE_URL; migration 0004 — NOBYPASSRLS, member
of `pv_app` so it has the API's RLS-bound grants, plus INSERT on the `positions` hypertable guarded by the
`positions_org_guard` trigger) and every transaction runs `set_config('app.org_id', <org>, true)` first, exactly
like the API, so a worker bug can't touch another org's rows. Ingest updates are guarded on
`status = 'ingesting'` so a redelivered task or a user who deleted the video in the meantime is a no-op.
S8 result rows (positions / teams / players) are written by ``pitchvision_worker.results_db``.

Columns mirror apps/api migrations 0001–0007 (the API owns the schema). Job queries live in
pitchvision_worker.orchestrator.store; cross-org lookups go through the SECURITY DEFINER helpers below.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from decimal import Decimal
from functools import lru_cache
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Connection,
    Engine,
    Integer,
    MetaData,
    Numeric,
    Table,
    Text,
    create_engine,
    func,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, REAL, TIMESTAMP, UUID

metadata = MetaData()
videos = Table(
    "videos",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("org_id", UUID(as_uuid=True), nullable=False),
    Column("title", Text),
    Column("status", Text, nullable=False),
    Column("source_uri", Text),
    Column("duration_ms", Integer),
    Column("fps", Numeric(6, 3)),
    Column("width", Integer),
    Column("height", Integer),
    Column("object_key", Text),
    Column("size_bytes", BigInteger),
    Column("thumbnail_url", Text),
    Column("error", JSONB),
    Column("deleted_at", TIMESTAMP(timezone=True)),
    Column("revision", Integer, nullable=False),
)
TZ = TIMESTAMP(timezone=True)
jobs = Table(
    "jobs",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("video_id", UUID(as_uuid=True), nullable=False),
    Column("org_id", UUID(as_uuid=True), nullable=False),
    Column("image_tag", Text, nullable=False),
    Column("config", JSONB, nullable=False),
    Column("status", Text, nullable=False),
    Column("progress", Numeric(5, 2), nullable=False),
    Column("fps_agg", Numeric),
    Column("error", JSONB),
    Column("created_at", TZ),
    Column("started_at", TZ),
    Column("finished_at", TZ),
    Column("created_by", UUID(as_uuid=True)),
    Column("stage", Text),
    Column("detail", JSONB),
    Column("coverage", JSONB),
)
job_tasks = Table(
    "job_tasks",
    metadata,
    Column("job_id", UUID(as_uuid=True), primary_key=True),
    Column("org_id", UUID(as_uuid=True), nullable=False),
    Column("task_key", Text, primary_key=True),
    Column("backend", Text),
    Column("external_id", Text),
    Column("status", Text, nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("error", Text),
    Column("started_at", TZ),
    Column("finished_at", TZ),
    Column("payload", JSONB),
    Column("not_before", TZ),
    Column("dispatched_at", TZ),
    Column("updated_at", TZ),
    Column("running_since", TZ),  # 0011
)
shots = Table(
    "shots",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
    Column("video_id", UUID(as_uuid=True), nullable=False),
    Column("org_id", UUID(as_uuid=True), nullable=False),
    Column("t0_ms", Integer, nullable=False),
    Column("t1_ms", Integer, nullable=False),
    Column("type", Text, nullable=False),
    Column("conf", REAL, nullable=False),
)
teams = Table(
    "teams",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
    Column("video_id", UUID(as_uuid=True), nullable=False),
    Column("org_id", UUID(as_uuid=True), nullable=False),
    Column("cluster", Integer),
    Column("name", Text),
    Column("color", Text),
    Column("confirmed", Boolean, nullable=False, server_default=text("false")),
    Column("attack_h1", Text),
    Column("attack_source", Text),
)
players = Table(
    "players",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
    Column("video_id", UUID(as_uuid=True), nullable=False),
    Column("org_id", UUID(as_uuid=True), nullable=False),
    Column("track_id", Integer, nullable=False),
    Column("team_id", UUID(as_uuid=True)),
    Column("role", Text),
    Column("confirmed", Boolean, nullable=False, server_default=text("false")),
    Column("frames", Integer),
    Column("t0_ms", Integer),
    Column("t1_ms", Integer),
)

stats_player = Table(  # 0001; written by S9 (pitchvision_worker.stages.stats) inside the S8 transaction
    "stats_player",
    metadata,
    Column("video_id", UUID(as_uuid=True), primary_key=True),
    Column("org_id", UUID(as_uuid=True), nullable=False),
    Column("player_id", UUID(as_uuid=True), primary_key=True),
    Column("revision", Integer, nullable=False, server_default=text("0")),
    Column("metrics", JSONB, nullable=False),
)

stats_team = Table(  # 0001; S9 possession / passes per team, written inside the S8 transaction
    "stats_team",
    metadata,
    Column("video_id", UUID(as_uuid=True), primary_key=True),
    Column("org_id", UUID(as_uuid=True), nullable=False),
    Column("team_id", UUID(as_uuid=True), primary_key=True),
    Column("revision", Integer, nullable=False, server_default=text("0")),
    Column("metrics", JSONB, nullable=False),
)

exports = Table(  # 0005 (SCR-25 / SCR-26); rendered by pitchvision_worker.exports
    "exports",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("org_id", UUID(as_uuid=True), nullable=False),
    Column("video_id", UUID(as_uuid=True), nullable=False),
    Column("kind", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("progress", Numeric(5, 2), nullable=False),
    Column("spec", JSONB, nullable=False),
    Column("filename", Text, nullable=False),
    Column("object_key", Text),
    Column("size_bytes", BigInteger),
    Column("error", JSONB),
    Column("attempts", Integer, nullable=False),
    Column("created_by", UUID(as_uuid=True)),
    Column("created_at", TZ),
    Column("started_at", TZ),
    Column("heartbeat_at", TZ),
    Column("finished_at", TZ),
    Column("expires_at", TZ),
    Column("deleted_at", TZ),
)

notifications = Table(  # 0006 (SCR-51); written by pitchvision_worker.notify
    "notifications",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
    Column("org_id", UUID(as_uuid=True), nullable=False),
    Column("user_id", UUID(as_uuid=True), nullable=False),
    Column("type", Text, nullable=False),
    Column("video_id", UUID(as_uuid=True)),
    Column("data", JSONB, nullable=False),
    Column("dedupe_key", Text),
    Column("created_at", TZ),
    Column("read_at", TZ),
    Column("email_status", Text, nullable=False),
    Column("email_attempts", Integer, nullable=False),
)


@lru_cache(maxsize=4)
def engine_for(url: str) -> Engine:
    # small pool: one Celery process handles one task at a time
    return create_engine(url, pool_pre_ping=True, pool_size=2, max_overflow=2)


@contextmanager
def org_tx(engine: Engine, org_id: uuid.UUID) -> Iterator[Connection]:
    """One transaction with app.org_id set (SET LOCAL semantics) so RLS applies."""
    with engine.begin() as conn:
        conn.execute(text("SELECT set_config('app.org_id', :org, true)"), {"org": str(org_id)})
        yield conn


def get_status(engine: Engine, org_id: uuid.UUID, video_id: uuid.UUID) -> str | None:
    with org_tx(engine, org_id) as conn:
        stmt = select(videos.c.status).where(videos.c.id == video_id, videos.c.org_id == org_id)
        status: str | None = conn.execute(stmt).scalar_one_or_none()
        return status


def mark_ready(
    engine: Engine,
    org_id: uuid.UUID,
    video_id: uuid.UUID,
    *,
    object_key: str,
    size_bytes: int,
    duration_ms: int | None,
    fps: float | None,
    width: int | None,
    height: int | None,
    title: str | None,
    thumbnail_url: str | None,
) -> bool:
    values: dict[str, Any] = {
        "status": "ready",
        "object_key": object_key,
        "size_bytes": size_bytes,
        "duration_ms": duration_ms,
        "fps": Decimal(str(round(fps, 3))) if fps is not None else None,
        "width": width,
        "height": height,
        "thumbnail_url": thumbnail_url,
        "error": None,
        "title": func.coalesce(videos.c.title, title[:200] if title else None),  # user's title wins
    }
    return _guarded_update(engine, org_id, video_id, values)


def mark_failed(engine: Engine, org_id: uuid.UUID, video_id: uuid.UUID, error: Mapping[str, Any]) -> bool:
    return _guarded_update(engine, org_id, video_id, {"status": "failed", "error": dict(error)})


def _guarded_update(engine: Engine, org_id: uuid.UUID, video_id: uuid.UUID, values: dict[str, Any]) -> bool:
    with org_tx(engine, org_id) as conn:
        stmt = (
            update(videos)
            .where(videos.c.id == video_id, videos.c.org_id == org_id, videos.c.status == "ingesting")
            .values(**values)
        )
        return conn.execute(stmt).rowcount == 1


# ---------------------------------------------------------------- cross-org lookups (migration 0003)
def active_jobs(engine: Engine) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """(job_id, org_id) of every job the orchestrator must keep driving (SECURITY DEFINER, ids only)."""
    with engine.begin() as conn:
        return [(r[0], r[1]) for r in conn.execute(text("SELECT job_id, org_id FROM pv_active_jobs()"))]


def job_org(engine: Engine, job_id: uuid.UUID) -> uuid.UUID | None:
    with engine.begin() as conn:
        org: uuid.UUID | None = conn.execute(text("SELECT pv_job_org(:j)"), {"j": job_id}).scalar_one_or_none()
        return org
