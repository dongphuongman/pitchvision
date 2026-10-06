"""jobs/job_tasks: orchestration bookkeeping (M1-BE-09/10).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-06

jobs
- created_by    user who started the job
- stage         current pipeline stage for the UI (ingest, normalize, shots, calibrate, infer, stitch, …)
- detail        live progress detail: {segments: {done, running, total}, fps, eta_s}
- coverage      {main_wide_pct} once S2b has run (stats are shown with this %)
- ux_jobs_video_active: at most one non-terminal job per video (the API answers 409 CONFLICT)

job_tasks (one row per stage / segment — the orchestrator's durable state, AD §16.1)
- payload       stage result summary (segment plan, normalize probe, …), written when the task is done
- not_before    retry backoff: the task is not re-dispatched before this time
- dispatched_at when the current attempt was handed to a ComputeBackend
- updated_at

Recovery helpers (SECURITY DEFINER, owned by the migration role so they see past RLS; they return ids only):
- pv_active_jobs() → (job_id, org_id) of jobs the orchestrator must keep driving: non-terminal jobs plus cancelled
                     jobs that still have dispatched tasks to revoke. Used by the startup/periodic sweep.
- pv_job_org(job)  → org_id of a job, so a message that only carries a job id can open an RLS-scoped transaction.
The migration role must be a superuser or have BYPASSRLS (compose: POSTGRES_USER; cloud: the schema owner).
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "pv_app"
ACTIVE = "'queued', 'ingesting', 'calibrating', 'inferring', 'postprocessing'"


def upgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE jobs
          ADD COLUMN created_by uuid,
          ADD COLUMN stage text,
          ADD COLUMN detail jsonb,
          ADD COLUMN coverage jsonb;
        CREATE UNIQUE INDEX ux_jobs_video_active ON jobs (video_id) WHERE status IN ({ACTIVE});

        ALTER TABLE job_tasks
          ADD COLUMN payload jsonb,
          ADD COLUMN not_before timestamptz,
          ADD COLUMN dispatched_at timestamptz,
          ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now();

        CREATE FUNCTION pv_active_jobs() RETURNS TABLE (job_id uuid, org_id uuid)
          LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
            SELECT j.id, j.org_id FROM public.jobs j
             WHERE j.status IN ({ACTIVE})
                OR (j.status = 'cancelled' AND EXISTS (
                      SELECT 1 FROM public.job_tasks t
                       WHERE t.job_id = j.id AND t.status IN ('queued', 'running') AND t.external_id IS NOT NULL))
             ORDER BY j.created_at
          $$;
        CREATE FUNCTION pv_job_org(p_job uuid) RETURNS uuid
          LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
            SELECT org_id FROM public.jobs WHERE id = p_job
          $$;
        REVOKE ALL ON FUNCTION pv_active_jobs() FROM PUBLIC;
        REVOKE ALL ON FUNCTION pv_job_org(uuid) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION pv_active_jobs() TO {APP_ROLE};
        GRANT EXECUTE ON FUNCTION pv_job_org(uuid) TO {APP_ROLE};
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP FUNCTION IF EXISTS pv_job_org(uuid);
        DROP FUNCTION IF EXISTS pv_active_jobs();
        ALTER TABLE job_tasks
          DROP COLUMN updated_at,
          DROP COLUMN dispatched_at,
          DROP COLUMN not_before,
          DROP COLUMN payload;
        DROP INDEX IF EXISTS ux_jobs_video_active;
        ALTER TABLE jobs
          DROP COLUMN coverage,
          DROP COLUMN detail,
          DROP COLUMN stage,
          DROP COLUMN created_by;
        """
    )
