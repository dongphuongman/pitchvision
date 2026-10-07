"""member_events (SCR-51 "X joined Org") and users.analysis_defaults (SCR-40 R02 / SCR-12 E10).

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-07

- member_events       one row per new membership (invite accepted, at sign-up or later). ``recipients`` = the org's
                      admins + owners at that moment, minus the new member. The org's regional worker pulls
                      undelivered rows (``GET /internal/v1/member-events?region=``), writes ``member_joined``
                      notifications, then acks (``POST /internal/v1/member-events/{id}/done``). No FK: an org
                      deleted meanwhile just has nothing to deliver.
- users.analysis_defaults   the user's SCR-12 defaults ``{mode, confidence}``; ``{}`` = product defaults.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE users ADD COLUMN analysis_defaults jsonb NOT NULL DEFAULT '{}'::jsonb;
        CREATE TABLE member_events (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id uuid NOT NULL,
          region text NOT NULL,
          user_id uuid NOT NULL,
          user_name text NOT NULL,
          org_name text NOT NULL,
          role text NOT NULL,
          recipients uuid[] NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          delivered_at timestamptz
        );
        CREATE INDEX member_events_pending_idx ON member_events (region, created_at) WHERE delivered_at IS NULL;
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS member_events; ALTER TABLE users DROP COLUMN IF EXISTS analysis_defaults;")
