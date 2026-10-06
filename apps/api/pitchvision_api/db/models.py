"""SQLAlchemy 2.0 models mirroring migrations 0001+ (AD §9.3). The migration is the source of truth for DDL."""
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKeyConstraint,
    Index,
    MetaData,
    Numeric,
    SmallInteger,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, REAL, TIMESTAMP, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

TZ = TIMESTAMP(timezone=True)
JOB_ACTIVE = ("queued", "ingesting", "calibrating", "inferring", "postprocessing")
JOB_ACTIVE_SQL = ", ".join(f"'{s}'" for s in JOB_ACTIVE)
GEN_UUID = text("gen_random_uuid()")


def _video_fk() -> ForeignKeyConstraint:
    return ForeignKeyConstraint(["video_id", "org_id"], ["videos.id", "videos.org_id"], ondelete="CASCADE")


class Base(DeclarativeBase):
    type_annotation_map = {  # noqa: RUF012
        uuid.UUID: UUID(as_uuid=True),
        str: Text(),
        datetime: TZ,
        dict[str, Any]: JSONB(),
        float: REAL(),
    }


class Video(Base):
    __tablename__ = "videos"
    __table_args__ = (
        UniqueConstraint("id", "org_id", name="uq_videos_id_org"),
        CheckConstraint("source_type IN ('upload', 'youtube', 'rtsp', 'import')"),
        CheckConstraint(
            "status IN ('uploading', 'ingesting', 'ready', 'processing', 'done', 'failed', 'deleted')",
            name="videos_status_check",
        ),
        CheckConstraint("size_bytes >= 0"),
        Index("ix_videos_org_created", "org_id", "created_at", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=GEN_UUID)
    org_id: Mapped[uuid.UUID]
    created_by: Mapped[uuid.UUID]
    source_type: Mapped[str | None]
    source_uri: Mapped[str | None]
    title: Mapped[str | None]
    duration_ms: Mapped[int | None]
    fps: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))
    width: Mapped[int | None]
    height: Mapped[int | None]
    status: Mapped[str] = mapped_column(server_default="uploading")
    revision: Mapped[int] = mapped_column(server_default="0")
    rights_confirmed_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    deleted_at: Mapped[datetime | None]
    # 0002: source object + upload/ingest bookkeeping
    object_key: Mapped[str | None]
    upload_id: Mapped[str | None]
    size_bytes: Mapped[int | None] = mapped_column(BigInteger())
    thumbnail_url: Mapped[str | None]
    error: Mapped[dict[str, Any] | None]


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("id", "org_id", name="uq_jobs_id_org"),
        _video_fk(),
        CheckConstraint(
            "status IN ('queued', 'ingesting', 'calibrating', 'inferring', 'postprocessing', 'done', 'failed', "
            "'cancelled')"
        ),
        Index("ix_jobs_video", "video_id"),
        Index("ix_jobs_org_status", "org_id", "status"),
        # 0003: one non-terminal job per video
        Index("ux_jobs_video_active", "video_id", unique=True, postgresql_where=text(f"status IN ({JOB_ACTIVE_SQL})")),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=GEN_UUID)
    video_id: Mapped[uuid.UUID]
    org_id: Mapped[uuid.UUID]
    image_tag: Mapped[str]
    config: Mapped[dict[str, Any]]
    status: Mapped[str] = mapped_column(server_default="queued")
    progress: Mapped[Decimal] = mapped_column(Numeric(5, 2), server_default="0")
    fps_agg: Mapped[Decimal | None] = mapped_column(Numeric())
    error: Mapped[dict[str, Any] | None]
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    # 0003: orchestration bookkeeping
    created_by: Mapped[uuid.UUID | None]
    stage: Mapped[str | None]
    detail: Mapped[dict[str, Any] | None]
    coverage: Mapped[dict[str, Any] | None]


class JobTask(Base):
    __tablename__ = "job_tasks"
    __table_args__ = (
        ForeignKeyConstraint(["job_id", "org_id"], ["jobs.id", "jobs.org_id"], ondelete="CASCADE"),
        CheckConstraint("status IN ('queued', 'running', 'done', 'failed')"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    org_id: Mapped[uuid.UUID]
    task_key: Mapped[str] = mapped_column(primary_key=True)
    backend: Mapped[str | None]
    external_id: Mapped[str | None]
    status: Mapped[str] = mapped_column(server_default="queued")
    attempts: Mapped[int] = mapped_column(server_default="0")
    error: Mapped[str | None]
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    # 0003
    payload: Mapped[dict[str, Any] | None]
    not_before: Mapped[datetime | None]
    dispatched_at: Mapped[datetime | None]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Team(Base):
    __tablename__ = "teams"
    __table_args__ = (
        UniqueConstraint("id", "org_id", name="uq_teams_id_org"),
        _video_fk(),
        Index("ix_teams_video", "video_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=GEN_UUID)
    video_id: Mapped[uuid.UUID]
    org_id: Mapped[uuid.UUID]
    cluster: Mapped[int | None]
    name: Mapped[str | None]
    color: Mapped[str | None]
    confirmed: Mapped[bool] = mapped_column(server_default="false")


class Player(Base):
    __tablename__ = "players"
    __table_args__ = (
        UniqueConstraint("video_id", "track_id", name="uq_players_video_track"),
        _video_fk(),
        # SET NULL (team_id) (PG15+) keeps org_id when the team goes away
        ForeignKeyConstraint(["team_id", "org_id"], ["teams.id", "teams.org_id"], ondelete="SET NULL (team_id)"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=GEN_UUID)
    video_id: Mapped[uuid.UUID]
    org_id: Mapped[uuid.UUID]
    track_id: Mapped[int]
    team_id: Mapped[uuid.UUID | None]
    role: Mapped[str | None]
    jersey: Mapped[int | None]
    jersey_conf: Mapped[float | None]
    name: Mapped[str | None]
    confirmed: Mapped[bool] = mapped_column(server_default="false")


class Position(Base):
    """TimescaleDB hypertable on ts_ms (no PK in the DB; the mapper key is logical only).

    Compressed hypertables can't have RLS, so pv_app has no grant here — the API reads `positions_org`.
    """

    __tablename__ = "positions"
    __table_args__ = (
        Index("positions_ts_ms_idx", text("ts_ms DESC")),  # created by create_hypertable
        Index("ix_positions_video_ts", "video_id", "ts_ms"),
    )
    __mapper_args__ = {"primary_key": ["video_id", "ts_ms", "track_id"]}  # noqa: RUF012

    video_id: Mapped[uuid.UUID]
    org_id: Mapped[uuid.UUID]
    ts_ms: Mapped[int]
    track_id: Mapped[int]
    team: Mapped[int | None] = mapped_column(SmallInteger())
    px: Mapped[float | None]
    py: Mapped[float | None]
    vx: Mapped[float | None]
    vy: Mapped[float | None]
    q: Mapped[float | None]


class Correction(Base):
    __tablename__ = "corrections"
    __table_args__ = (_video_fk(), Index("ix_corrections_video_revision", "video_id", "revision"))

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=GEN_UUID)
    video_id: Mapped[uuid.UUID]
    org_id: Mapped[uuid.UUID]
    revision: Mapped[int]
    type: Mapped[str]
    payload: Mapped[dict[str, Any]]
    created_by: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class StatsPlayer(Base):
    __tablename__ = "stats_player"
    __table_args__ = (_video_fk(),)

    video_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    org_id: Mapped[uuid.UUID]
    player_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    revision: Mapped[int] = mapped_column(server_default="0")
    metrics: Mapped[dict[str, Any]]


class StatsTeam(Base):
    __tablename__ = "stats_team"
    __table_args__ = (_video_fk(),)

    video_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    org_id: Mapped[uuid.UUID]
    team_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    revision: Mapped[int] = mapped_column(server_default="0")
    metrics: Mapped[dict[str, Any]]


class Tactic(Base):
    __tablename__ = "tactics"
    __table_args__ = (_video_fk(), Index("ix_tactics_video_revision_kind", "video_id", "revision", "kind"))

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=GEN_UUID)
    video_id: Mapped[uuid.UUID]
    org_id: Mapped[uuid.UUID]
    revision: Mapped[int] = mapped_column(server_default="0")
    kind: Mapped[str]
    data: Mapped[dict[str, Any]]


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (_video_fk(), Index("ix_reports_video", "video_id"))

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=GEN_UUID)
    video_id: Mapped[uuid.UUID]
    org_id: Mapped[uuid.UUID]
    lang: Mapped[str]
    content_md: Mapped[str | None]
    model: Mapped[str | None]
    facts_checked: Mapped[bool] = mapped_column(server_default="false")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ShotRow(Base):
    """Stage S2b output; mirrors pitchvision_schemas.Shot."""

    __tablename__ = "shots"
    __table_args__ = (
        _video_fk(),
        CheckConstraint("t0_ms >= 0"),
        CheckConstraint("t1_ms > t0_ms", name="ck_shots_ordered"),
        CheckConstraint("type IN ('main_wide', 'close_up', 'replay', 'other')"),
        CheckConstraint("conf >= 0 AND conf <= 1"),
        Index("ix_shots_video_t0", "video_id", "t0_ms"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=GEN_UUID)
    video_id: Mapped[uuid.UUID]
    org_id: Mapped[uuid.UUID]
    t0_ms: Mapped[int]
    t1_ms: Mapped[int]
    type: Mapped[str]
    conf: Mapped[float]


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (
        CheckConstraint("char_length(key) BETWEEN 1 AND 255"),
        Index("ix_idempotency_keys_created", "created_at"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(primary_key=True)
    method: Mapped[str]
    path: Mapped[str]
    request_hash: Mapped[str]
    response_status: Mapped[int | None]
    response_body: Mapped[Any] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


# Views live outside Base.metadata so autogenerate never tries to create them as tables.
view_metadata = MetaData()

positions_org = Table(
    "positions_org",
    view_metadata,
    *(Column(c.name, c.type) for c in Position.__table__.columns),
    comment="security-barrier view: positions WHERE org_id = app.org_id (the API's only access path)",
)


class Export(Base):
    """0005: SCR-25 / SCR-26 export requests (spec = pitchvision_schemas.ExportSpec)."""

    __tablename__ = "exports"
    __table_args__ = (
        _video_fk(),
        CheckConstraint("kind IN ('video')"),
        CheckConstraint("status IN ('queued', 'running', 'done', 'failed', 'cancelled')"),
        CheckConstraint("progress BETWEEN 0 AND 100"),
        Index("ix_exports_org_created", "org_id", "created_at", "id"),
        Index("ix_exports_purge", "expires_at", postgresql_where=text("object_key IS NOT NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=GEN_UUID)
    org_id: Mapped[uuid.UUID]
    video_id: Mapped[uuid.UUID]
    kind: Mapped[str] = mapped_column(server_default="video")
    status: Mapped[str] = mapped_column(server_default="queued")
    progress: Mapped[Decimal] = mapped_column(Numeric(5, 2), server_default="0")
    spec: Mapped[dict[str, Any]]
    filename: Mapped[str]
    object_key: Mapped[str | None]
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    error: Mapped[dict[str, Any] | None]
    attempts: Mapped[int] = mapped_column(server_default="0")
    created_by: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    started_at: Mapped[datetime | None]
    heartbeat_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    expires_at: Mapped[datetime | None]
    deleted_at: Mapped[datetime | None]


class Notification(Base):
    """0006: SCR-51 notifications (type = pitchvision_schemas.NotificationType)."""

    __tablename__ = "notifications"
    __table_args__ = (
        _video_fk(),
        CheckConstraint(
            "type IN ('job_ready', 'needs_teams', 'job_failed', 'export_ready', 'export_failed', 'quota_80', "
            "'quota_100')"
        ),
        CheckConstraint("email_status IN ('pending', 'sent', 'skipped', 'failed')"),
        UniqueConstraint("user_id", "dedupe_key", name="uq_notifications_dedupe"),
        Index("ix_notifications_user", "org_id", "user_id", "created_at", "id"),
        Index("ix_notifications_email", "created_at", postgresql_where=text("email_status = 'pending'")),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=GEN_UUID)
    org_id: Mapped[uuid.UUID]
    user_id: Mapped[uuid.UUID]
    type: Mapped[str]
    video_id: Mapped[uuid.UUID | None]
    data: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    dedupe_key: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    read_at: Mapped[datetime | None]
    email_status: Mapped[str] = mapped_column(server_default="pending")
    email_attempts: Mapped[int] = mapped_column(server_default="0")
