"""erasure_requests: deleted orgs / accounts whose regional data must still be purged (SCR-40 D03 / D04, DLG-08).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-07

Identity deletes the org / user row at once (memberships, invites, tokens cascade) and records a request here. Each
regional worker pulls the requests for its region (``GET /internal/v1/erasures?region=``), purges DB rows + objects,
and acks with ``POST /internal/v1/erasures/{id}/done``; ``completed_at`` is set once every region in ``regions``
acked (AD §14: within 24 h). No FK on ``subject_id`` — the subject is already gone; ``regions`` may be empty (an
account without memberships), then the request is complete at once.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE erasure_requests (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          kind text NOT NULL CHECK (kind IN ('org', 'user')),
          subject_id uuid NOT NULL,
          regions text[] NOT NULL,
          regions_done text[] NOT NULL DEFAULT '{}',
          requested_by uuid,
          requested_at timestamptz NOT NULL DEFAULT now(),
          completed_at timestamptz
        );
        CREATE INDEX erasure_requests_pending_idx ON erasure_requests (requested_at) WHERE completed_at IS NULL;
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS erasure_requests")
