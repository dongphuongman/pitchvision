"""job_tasks.running_since: when the orchestrator first saw the dispatched attempt actually running.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-07

Stage timeouts count from here, not from ``dispatched_at``: with fewer worker processes than dispatched segments a
task can wait in the broker queue longer than its timeout, and was failed as "timed out" the moment it started
(found on the Mac with a 14 min match: 16 segments dispatched, 2 cpu processes). NULL while queued; reset on every
dispatch. Queued tasks keep the separate "lost in queue" limit (6 h = broker visibility timeout).
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE job_tasks ADD COLUMN running_since timestamptz")


def downgrade() -> None:
    op.execute("ALTER TABLE job_tasks DROP COLUMN IF EXISTS running_since")
