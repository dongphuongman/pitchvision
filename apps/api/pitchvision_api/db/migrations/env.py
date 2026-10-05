"""Alembic environment: sync psycopg driver, run as the schema owner (never as pv_app)."""
import os

from alembic import context
from pitchvision_api.db.models import Base
from sqlalchemy import create_engine, pool

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    url = (
        config.get_main_option("sqlalchemy.url")
        or os.environ.get("PV_MIGRATE_DATABASE_URL")
        or os.environ.get("PV_DATABASE_URL")
    )
    if not url:
        raise RuntimeError("set PV_MIGRATE_DATABASE_URL (schema owner) or PV_DATABASE_URL")
    for prefix in ("postgresql+asyncpg://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url.removeprefix(prefix)
    return url


def run_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
