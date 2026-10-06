"""notifications: SCR-51 in-app notifications + their email delivery state (M1-BE-19).

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-06

- user_id       recipient (identity user id; the job's / export's requester, the member who crossed a quota mark)
- type          pitchvision_schemas.NotificationType (frozen copy below)
- data          {job_id, export_id, code, filename, used, limit, …} — the web renders the text from type + data
- dedupe_key    one notification per (user, key): job:{id}:done, quota_80:{yyyy-mm}, … (ON CONFLICT DO NOTHING)
- email_status  pending → sent | skipped | failed (identity-svc sends; ``skipped`` = in-app only type / no mailer)

pv_pending_notification_emails(limit) (SECURITY DEFINER, ids only) feeds the worker's delivery task, which then
works in an RLS-scoped transaction like everything else.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "pv_app"
ORG_GUC = "NULLIF(current_setting('app.org_id', true), '')::uuid"
TYPES = "'job_ready', 'needs_teams', 'job_failed', 'export_ready', 'export_failed', 'quota_80', 'quota_100'"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE notifications (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id uuid NOT NULL,
          user_id uuid NOT NULL,
          type text NOT NULL CHECK (type IN ({TYPES})),
          video_id uuid,
          data jsonb NOT NULL DEFAULT '{{}}'::jsonb,
          dedupe_key text,
          created_at timestamptz NOT NULL DEFAULT now(),
          read_at timestamptz,
          email_status text NOT NULL DEFAULT 'pending'
            CHECK (email_status IN ('pending', 'sent', 'skipped', 'failed')),
          email_attempts int NOT NULL DEFAULT 0,
          FOREIGN KEY (video_id, org_id) REFERENCES videos (id, org_id) ON DELETE CASCADE,
          CONSTRAINT uq_notifications_dedupe UNIQUE (user_id, dedupe_key)
        );
        CREATE INDEX ix_notifications_user ON notifications (org_id, user_id, created_at, id);
        CREATE INDEX ix_notifications_email ON notifications (created_at) WHERE email_status = 'pending';

        GRANT SELECT, INSERT, UPDATE, DELETE ON notifications TO {APP_ROLE};
        ALTER TABLE notifications ENABLE ROW LEVEL SECURITY;
        ALTER TABLE notifications FORCE ROW LEVEL SECURITY;
        CREATE POLICY org_isolation ON notifications USING (org_id = {ORG_GUC}) WITH CHECK (org_id = {ORG_GUC});

        CREATE FUNCTION pv_pending_notification_emails(p_limit int)
          RETURNS TABLE (notification_id uuid, org_id uuid)
          LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
            SELECT n.id, n.org_id FROM public.notifications n
             WHERE n.email_status = 'pending' ORDER BY n.created_at LIMIT p_limit
          $$;
        REVOKE ALL ON FUNCTION pv_pending_notification_emails(int) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION pv_pending_notification_emails(int) TO {APP_ROLE};
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP FUNCTION IF EXISTS pv_pending_notification_emails(int);
        DROP TABLE IF EXISTS notifications;
        """
    )
