"""exports: SCR-25 / SCR-26 render requests and their files (M1-BE-17, AD S11).

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-06

- spec          pitchvision_schemas.ExportSpec frozen at request time (job, range, layers, layout, size, watermark,
                team names / colours) — the worker renders from it alone
- status        queued → running → done | failed | cancelled; retry puts a failed / cancelled row back to queued
- object_key    keys.export_key once the file is uploaded; set back to NULL when the purge deleted it
- expires_at    finished_at + the plan's export TTL; the file is purged after it (the row stays for the history)
- deleted_at    the user removed it from SCR-26 (hidden; the file is purged)
- heartbeat_at  touched by the rendering worker with every progress update

pv_export_purge_candidates(limit) (SECURITY DEFINER, ids + keys only): rows whose file must go — expired or deleted
— for the worker's periodic purge, which then deletes the object and clears object_key in an RLS-scoped
transaction. pv_stale_exports(age_s) → (export_id, org_id) of `running` rows whose worker stopped sending heartbeats
(killed / OOM): the sweep fails them as retryable. Both granted to pv_app (so pv_worker, a member, has them too).
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "pv_app"
ORG_GUC = "NULLIF(current_setting('app.org_id', true), '')::uuid"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE exports (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id uuid NOT NULL,
          video_id uuid NOT NULL,
          kind text NOT NULL DEFAULT 'video' CHECK (kind IN ('video')),
          status text NOT NULL DEFAULT 'queued'
            CHECK (status IN ('queued', 'running', 'done', 'failed', 'cancelled')),
          progress numeric(5, 2) NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
          spec jsonb NOT NULL,
          filename text NOT NULL,
          object_key text,
          size_bytes bigint,
          error jsonb,
          attempts int NOT NULL DEFAULT 0,
          created_by uuid,
          created_at timestamptz NOT NULL DEFAULT now(),
          started_at timestamptz,
          heartbeat_at timestamptz,
          finished_at timestamptz,
          expires_at timestamptz,
          deleted_at timestamptz,
          FOREIGN KEY (video_id, org_id) REFERENCES videos (id, org_id) ON DELETE CASCADE
        );
        CREATE INDEX ix_exports_org_created ON exports (org_id, created_at, id);
        CREATE INDEX ix_exports_purge ON exports (expires_at) WHERE object_key IS NOT NULL;

        GRANT SELECT, INSERT, UPDATE, DELETE ON exports TO {APP_ROLE};
        ALTER TABLE exports ENABLE ROW LEVEL SECURITY;
        ALTER TABLE exports FORCE ROW LEVEL SECURITY;
        CREATE POLICY org_isolation ON exports USING (org_id = {ORG_GUC}) WITH CHECK (org_id = {ORG_GUC});

        CREATE FUNCTION pv_export_purge_candidates(p_limit int)
          RETURNS TABLE (export_id uuid, org_id uuid, object_key text)
          LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
            SELECT e.id, e.org_id, e.object_key FROM public.exports e
             WHERE e.object_key IS NOT NULL AND (e.deleted_at IS NOT NULL OR e.expires_at < now())
             ORDER BY e.expires_at NULLS FIRST
             LIMIT p_limit
          $$;
        CREATE FUNCTION pv_stale_exports(p_age_s int) RETURNS TABLE (export_id uuid, org_id uuid)
          LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
            SELECT e.id, e.org_id FROM public.exports e
             WHERE e.status = 'running'
               AND coalesce(e.heartbeat_at, e.started_at) < now() - make_interval(secs => p_age_s)
          $$;
        REVOKE ALL ON FUNCTION pv_export_purge_candidates(int) FROM PUBLIC;
        REVOKE ALL ON FUNCTION pv_stale_exports(int) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION pv_export_purge_candidates(int) TO {APP_ROLE};
        GRANT EXECUTE ON FUNCTION pv_stale_exports(int) TO {APP_ROLE};
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP FUNCTION IF EXISTS pv_stale_exports(int);
        DROP FUNCTION IF EXISTS pv_export_purge_candidates(int);
        DROP TABLE IF EXISTS exports;
        """
    )
