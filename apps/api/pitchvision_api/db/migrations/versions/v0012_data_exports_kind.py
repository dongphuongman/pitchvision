"""exports.kind may be 'data' (M3-BE-03): raw tracking + stats as JSON / a ZIP of CSVs (SCR-25 tab Data, SCR-20f).

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-08

The 0005 CHECK was inline, so Postgres named it ``exports_kind_check``.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE exports DROP CONSTRAINT exports_kind_check")
    op.execute("ALTER TABLE exports ADD CONSTRAINT exports_kind_check CHECK (kind IN ('video', 'data'))")


def downgrade() -> None:
    op.execute("DELETE FROM exports WHERE kind = 'data'")
    op.execute("ALTER TABLE exports DROP CONSTRAINT exports_kind_check")
    op.execute("ALTER TABLE exports ADD CONSTRAINT exports_kind_check CHECK (kind IN ('video'))")
