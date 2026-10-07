"""SCR-40 Data & privacy: data exports (D02) and the regional half of org / account erasure (D03 / D04, DLG-08).

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-07

- data_exports     one GDPR export archive per request (RLS). scope 'org' (admin+: the org's videos, analyses,
                   corrections, positions) or 'account' (the requester's own data). Built by the cpu worker
                   (``cpu.build_data_export``) into ``keys.data_export_key``; kept 7 days, then the file goes.
- erasures         the worker's progress on identity's erasure requests (no RLS, pv_worker only): first pass, final
                   pass ≥ 4 h later, then the ack to identity.
- videos.created_by  nullable: an erased account's ids are cleared from the org's rows (videos, jobs, corrections,
                   exports keep existing — they belong to the org).
- notifications.type += data_export_ready.

SECURITY DEFINER helpers (owned by the migration role, which bypasses RLS like the 0003 / 0005 / 0006 helpers):
- pv_purge_org(org)     deletes every row of the org in every table (positions included) → rows deleted.
- pv_purge_user(user)   deletes the user's notifications + data exports, clears created_by → data export keys.
- pv_data_export_purge_candidates(limit)  expired archives still holding a file (ids + keys only).
The purge functions are granted to pv_worker only (the API cannot purge); the candidates helper to pv_app.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "pv_app"
WORKER_ROLE = "pv_worker"
ORG_GUC = "NULLIF(current_setting('app.org_id', true), '')::uuid"
OLD_TYPES = "'job_ready', 'needs_teams', 'job_failed', 'export_ready', 'export_failed', 'quota_80', 'quota_100'"
TYPES = OLD_TYPES + ", 'data_export_ready'"
# children first; videos last (most rows above also cascade from it)
ORG_TABLES = ("notifications", "data_exports", "exports", "idempotency_keys", "reports", "tactics", "stats_team",
              "stats_player", "shots", "corrections", "players", "teams", "job_tasks", "jobs", "videos")  # fmt: skip


def upgrade() -> None:
    purge_org = "\n".join(
        f"  DELETE FROM public.{t} WHERE org_id = p_org; GET DIAGNOSTICS c = ROW_COUNT; n := n + c;" for t in ORG_TABLES
    )
    op.execute(
        f"""
        ALTER TABLE videos ALTER COLUMN created_by DROP NOT NULL;
        ALTER TABLE notifications DROP CONSTRAINT notifications_type_check;
        ALTER TABLE notifications ADD CONSTRAINT notifications_type_check CHECK (type IN ({TYPES}));

        CREATE TABLE data_exports (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id uuid NOT NULL,
          user_id uuid NOT NULL,
          scope text NOT NULL CHECK (scope IN ('org', 'account')),
          status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'failed')),
          object_key text,
          size_bytes bigint,
          error jsonb,
          created_at timestamptz NOT NULL DEFAULT now(),
          started_at timestamptz,
          finished_at timestamptz,
          expires_at timestamptz
        );
        CREATE INDEX ix_data_exports_user ON data_exports (org_id, user_id, created_at);
        CREATE INDEX ix_data_exports_purge ON data_exports (expires_at) WHERE object_key IS NOT NULL;
        GRANT SELECT, INSERT, UPDATE, DELETE ON data_exports TO {APP_ROLE};
        ALTER TABLE data_exports ENABLE ROW LEVEL SECURITY;
        ALTER TABLE data_exports FORCE ROW LEVEL SECURITY;
        CREATE POLICY org_isolation ON data_exports USING (org_id = {ORG_GUC}) WITH CHECK (org_id = {ORG_GUC});

        CREATE TABLE erasures (
          request_id uuid PRIMARY KEY,
          kind text NOT NULL CHECK (kind IN ('org', 'user')),
          subject_id uuid NOT NULL,
          passes int NOT NULL DEFAULT 0,
          last_pass_at timestamptz,
          acked_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now()
        );
        GRANT SELECT, INSERT, UPDATE ON erasures TO {WORKER_ROLE};

        CREATE FUNCTION pv_purge_org(p_org uuid) RETURNS bigint
          LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
        DECLARE n bigint := 0; c bigint;
        BEGIN
          DELETE FROM public.positions WHERE org_id = p_org; GET DIAGNOSTICS c = ROW_COUNT; n := n + c;
        {purge_org}
          RETURN n;
        END $$;

        CREATE FUNCTION pv_purge_user(p_user uuid) RETURNS SETOF text
          LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
        BEGIN
          DELETE FROM public.notifications WHERE user_id = p_user;
          UPDATE public.videos SET created_by = NULL WHERE created_by = p_user;
          UPDATE public.jobs SET created_by = NULL WHERE created_by = p_user;
          UPDATE public.corrections SET created_by = NULL WHERE created_by = p_user;
          UPDATE public.exports SET created_by = NULL WHERE created_by = p_user;
          RETURN QUERY DELETE FROM public.data_exports WHERE user_id = p_user AND object_key IS NOT NULL
                       RETURNING object_key;
          DELETE FROM public.data_exports WHERE user_id = p_user;
        END $$;

        CREATE FUNCTION pv_data_export_purge_candidates(p_limit int)
          RETURNS TABLE (export_id uuid, org_id uuid, object_key text)
          LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
            SELECT d.id, d.org_id, d.object_key FROM public.data_exports d
             WHERE d.object_key IS NOT NULL AND d.expires_at < now() ORDER BY d.expires_at LIMIT p_limit
          $$;

        REVOKE ALL ON FUNCTION pv_purge_org(uuid) FROM PUBLIC;
        REVOKE ALL ON FUNCTION pv_purge_user(uuid) FROM PUBLIC;
        REVOKE ALL ON FUNCTION pv_data_export_purge_candidates(int) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION pv_purge_org(uuid) TO {WORKER_ROLE};
        GRANT EXECUTE ON FUNCTION pv_purge_user(uuid) TO {WORKER_ROLE};
        GRANT EXECUTE ON FUNCTION pv_data_export_purge_candidates(int) TO {APP_ROLE};
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        DROP FUNCTION IF EXISTS pv_data_export_purge_candidates(int);
        DROP FUNCTION IF EXISTS pv_purge_user(uuid);
        DROP FUNCTION IF EXISTS pv_purge_org(uuid);
        DROP TABLE IF EXISTS erasures;
        DROP TABLE IF EXISTS data_exports;
        DELETE FROM notifications WHERE type = 'data_export_ready';
        ALTER TABLE notifications DROP CONSTRAINT notifications_type_check;
        ALTER TABLE notifications ADD CONSTRAINT notifications_type_check CHECK (type IN ({OLD_TYPES}));
        """
    )
    # videos.created_by stays nullable: rows of erased accounts cannot get an id back
