"""subscriptions: an org's plan (AD §9.4, UIUX SCR-41). No row = Free.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-06

Billing (Stripe) is not wired yet: rows are written by ops / the future webhook. ``minutes_per_month`` overrides the
plan's monthly allowance (Team plans are sold per contract). The plan list is a frozen copy of
``pitchvision_schemas.Plan`` at this revision.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DDL = """
CREATE TABLE subscriptions (
  org_id uuid PRIMARY KEY REFERENCES orgs (id) ON DELETE CASCADE,
  plan text NOT NULL DEFAULT 'free' CHECK (plan IN ('free', 'pro', 'team')),
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'past_due', 'canceled')),
  minutes_per_month int CHECK (minutes_per_month IS NULL OR minutes_per_month >= 0),
  external_id text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
"""


def upgrade() -> None:
    op.execute(DDL)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS subscriptions")
