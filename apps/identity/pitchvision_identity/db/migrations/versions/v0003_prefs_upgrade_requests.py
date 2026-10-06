"""users.email_prefs (SCR-40 Preferences R03) and upgrade_requests (SCR-41 waitlist while billing is not wired).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-06

- users.email_prefs   {job_ready, job_failed, export_ready: bool}; a missing key = on. Checked by the internal
                      notification-email route.
- upgrade_requests    "Choose Pro" / "Contact sales" / "Notify owner" clicks: who, which plan, why (reason from the
                      blocked action: quota | feature | seats | quality). Ops follow up by hand until Stripe.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE users ADD COLUMN email_prefs jsonb NOT NULL DEFAULT '{}'::jsonb;
        CREATE TABLE upgrade_requests (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id uuid NOT NULL REFERENCES orgs (id) ON DELETE CASCADE,
          user_id uuid REFERENCES users (id) ON DELETE SET NULL,
          plan text NOT NULL CHECK (plan IN ('pro', 'team')),
          period text NOT NULL CHECK (period IN ('monthly', 'annual')),
          reason text CHECK (reason IN ('quota', 'feature', 'seats', 'quality')),
          owners_notified int NOT NULL DEFAULT 0,
          created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX upgrade_requests_org_id_idx ON upgrade_requests (org_id, created_at);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS upgrade_requests; ALTER TABLE users DROP COLUMN IF EXISTS email_prefs;")
