"""Tiny videos repository for workers (SQLAlchemy Core, sync psycopg 3).

Design (M1-BE-08): the ingest worker writes the outcome straight to Postgres instead of bouncing it
through the API. It connects with an RLS-bound role (PV_WORKER_DATABASE_URL; `pv_app` today — no
BYPASSRLS) and every transaction runs `set_config('app.org_id', <org>, true)` first, exactly like the API,
so a worker bug can't touch another org's rows. Updates are guarded on `status = 'ingesting'` so a
redelivered task or a user who deleted the video in the meantime is a no-op.
TODO(OPS): a dedicated `pv_worker` role with UPDATE-only grants on the columns workers own.

Columns mirror apps/api migrations 0001/0002 (the API owns the schema).
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
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID

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
