"""M1-BE-03: migration 0001 against real TimescaleDB — tables, hypertable, RLS, model parity, downgrade."""
import uuid
from collections.abc import AsyncIterator

import psycopg
import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from pitchvision_api.db import make_engine, make_sessionmaker, org_session
from pitchvision_api.db.models import Base, Job, Position, ShotRow, Video, positions_org
from pitchvision_schemas import JobStatus, ShotType
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from .conftest import Db, alembic_config, enable_app_login

TENANT_TABLES = {
    "videos", "jobs", "job_tasks", "teams", "players", "positions", "corrections",
    "stats_player", "stats_team", "tactics", "reports", "shots", "idempotency_keys",
}  # fmt: skip
ORG_A, ORG_B = uuid.uuid4(), uuid.uuid4()


@pytest.fixture
async def app_engine(migrated: Db) -> AsyncIterator[AsyncEngine]:
    engine = make_engine(migrated.app_url)
    yield engine
    await engine.dispose()


@pytest.fixture
def sm(app_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return make_sessionmaker(app_engine)


def _query(db: Db, sql: str) -> list[tuple[object, ...]]:
    with psycopg.connect(db.conninfo()) as conn:
        return conn.execute(sql).fetchall()  # type: ignore[arg-type]


async def _video(sm: async_sessionmaker[AsyncSession], org: uuid.UUID) -> uuid.UUID:
    async with org_session(sm, org) as s:
        v = Video(org_id=org, created_by=uuid.uuid4(), status="ready", title="match")
        s.add(v)
        await s.flush()
        return v.id


def test_all_tables_exist(migrated: Db) -> None:
    rows = _query(migrated, "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
    assert {r[0] for r in rows} >= TENANT_TABLES


def test_positions_is_compressed_hypertable(migrated: Db) -> None:
    rows = _query(
        migrated,
        "SELECT d.column_name, d.integer_interval FROM timescaledb_information.dimensions d "
        "WHERE d.hypertable_name = 'positions'",
    )
    assert rows == [("ts_ms", 600000)]
    seg = _query(
        migrated,
        "SELECT attname FROM timescaledb_information.compression_settings "
        "WHERE hypertable_name = 'positions' AND segmentby_column_index IS NOT NULL ORDER BY segmentby_column_index",
    )
    assert [r[0] for r in seg] == ["video_id", "track_id"]


def test_every_tenant_table_has_forced_rls_and_policy(migrated: Db) -> None:
    rows = _query(
        migrated,
        "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
        "  EXISTS (SELECT 1 FROM pg_policies p WHERE p.tablename = c.relname AND p.policyname = 'org_isolation') "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')",
    )
    by_name = {r[0]: r[1:] for r in rows}
    for table in TENANT_TABLES - {"positions"}:
        assert by_name[table] == (True, True, True), table


async def test_positions_only_via_org_view(migrated: Db, sm: async_sessionmaker[AsyncSession]) -> None:
    """Compressed hypertable can't carry RLS: pv_app has no grant on it and reads the org-filtered view."""
    vid_a, vid_b = uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(migrated.conninfo()) as conn:  # workers write positions; owner stands in here
        conn.execute(
            "INSERT INTO positions (video_id, org_id, ts_ms, track_id, px, py) VALUES "
            "(%s, %s, 0, 1, 10, 20), (%s, %s, 700000, 1, 11, 21), (%s, %s, 0, 2, 30, 40)",
            (vid_a, ORG_A, vid_a, ORG_A, vid_b, ORG_B),
        )
    async with org_session(sm, ORG_A) as s:
        rows = (await s.execute(select(positions_org.c.video_id).where(positions_org.c.video_id.in_([vid_a, vid_b]))))
        assert {r[0] for r in rows} == {vid_a}
    async with org_session(sm, ORG_B) as s:
        cnt = select(func.count()).select_from(positions_org).where(positions_org.c.video_id.in_([vid_a, vid_b]))
        assert await s.scalar(cnt) == 1
    with pytest.raises(DBAPIError, match="permission denied"):
        async with org_session(sm, ORG_A) as s:
            await s.execute(select(func.count()).select_from(Position))
    with pytest.raises(DBAPIError, match="permission denied"):
        async with org_session(sm, ORG_A) as s:
            s.add(Position(video_id=vid_a, org_id=ORG_A, ts_ms=5, track_id=1))


def test_app_role_cannot_bypass_rls(migrated: Db) -> None:
    rows = _query(migrated, "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'pv_app'")
    assert rows == [(False, False)]


async def test_rls_isolates_videos_and_shots(sm: async_sessionmaker[AsyncSession]) -> None:
    vid = await _video(sm, ORG_A)
    async with org_session(sm, ORG_A) as s:
        s.add(ShotRow(video_id=vid, org_id=ORG_A, t0_ms=0, t1_ms=4000, type=ShotType.main_wide, conf=0.97))

    async with org_session(sm, ORG_A) as s:
        assert await s.scalar(select(func.count()).select_from(Video).where(Video.id == vid)) == 1
        assert await s.scalar(select(func.count()).select_from(ShotRow).where(ShotRow.video_id == vid)) == 1
    async with org_session(sm, ORG_B) as s:
        assert await s.scalar(select(func.count()).select_from(Video).where(Video.id == vid)) == 0
        assert await s.scalar(select(func.count()).select_from(ShotRow).where(ShotRow.video_id == vid)) == 0


async def test_rls_rejects_insert_for_another_org(sm: async_sessionmaker[AsyncSession]) -> None:
    with pytest.raises(DBAPIError, match="row-level security"):
        async with org_session(sm, ORG_A) as s:
            s.add(Video(org_id=ORG_B, created_by=uuid.uuid4(), status="ready"))
    vid = await _video(sm, ORG_A)
    with pytest.raises(DBAPIError, match="row-level security"):
        async with org_session(sm, ORG_A) as s:
            s.add(ShotRow(video_id=vid, org_id=ORG_B, t0_ms=0, t1_ms=10, type="other", conf=0.5))


async def test_child_cannot_reference_other_orgs_video(sm: async_sessionmaker[AsyncSession]) -> None:
    vid_a = await _video(sm, ORG_A)
    with pytest.raises(IntegrityError, match="foreign key"):
        async with org_session(sm, ORG_B) as s:
            s.add(ShotRow(video_id=vid_a, org_id=ORG_B, t0_ms=0, t1_ms=10, type="other", conf=0.5))


async def test_no_org_context_sees_nothing(sm: async_sessionmaker[AsyncSession]) -> None:
    await _video(sm, ORG_A)
    async with sm() as s, s.begin():
        assert await s.scalar(select(func.count()).select_from(Video)) == 0


async def test_status_and_shot_checks(sm: async_sessionmaker[AsyncSession]) -> None:
    vid = await _video(sm, ORG_A)
    vids = [await _video(sm, ORG_A) for _ in JobStatus]  # 0003: one active job per video
    async with org_session(sm, ORG_A) as s:
        for v, st in zip(vids, JobStatus, strict=True):
            s.add(Job(video_id=v, org_id=ORG_A, image_tag="t", config={}, status=st))
        for i, t in enumerate(ShotType):
            s.add(ShotRow(video_id=vid, org_id=ORG_A, t0_ms=i * 10, t1_ms=i * 10 + 10, type=t, conf=1.0))
    bad = [
        Video(org_id=ORG_A, created_by=uuid.uuid4(), status="bogus"),
        Job(video_id=vid, org_id=ORG_A, image_tag="t", config={}, status="running"),
        ShotRow(video_id=vid, org_id=ORG_A, t0_ms=50, t1_ms=50, type="main_wide", conf=0.5),
        ShotRow(video_id=vid, org_id=ORG_A, t0_ms=0, t1_ms=50, type="wide", conf=0.5),
    ]
    for row in bad:
        with pytest.raises(IntegrityError, match="check constraint"):
            async with org_session(sm, ORG_A) as s:
                s.add(row)


def test_models_match_migration(migrated: Db) -> None:
    engine = create_engine(migrated.url())
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_server_default": False})
        diff = compare_metadata(ctx, Base.metadata)
    engine.dispose()
    assert not diff, "\n".join(map(repr, diff))


def test_downgrade_base_and_upgrade_again(migrated: Db) -> None:
    cfg = alembic_config(migrated)
    command.downgrade(cfg, "base")
    try:
        rows = _query(migrated, "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
        assert TENANT_TABLES.isdisjoint({r[0] for r in rows}), rows
        assert _query(migrated, "SELECT 1 FROM pg_roles WHERE rolname = 'pv_app'") == []
    finally:
        command.upgrade(cfg, "head")
        enable_app_login(migrated)
