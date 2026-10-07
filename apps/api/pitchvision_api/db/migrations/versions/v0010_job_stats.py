"""pv_job_stats(window): job counts by status across orgs, for /metrics (M1-OPS-08).

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-07

SECURITY DEFINER like the 0003 helpers (counts only, no ids): ``total`` = jobs in that status now, ``recent`` = jobs
that reached it (finished_at) within the last ``p_window_s`` seconds — the job failure-rate alert divides failed by
done + failed over 1 h. Granted to pv_app.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION pv_job_stats(p_window_s int) RETURNS TABLE (status text, total bigint, recent bigint)
          LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
            SELECT j.status, count(*),
                   count(*) FILTER (WHERE j.finished_at >= now() - make_interval(secs => p_window_s))
              FROM public.jobs j GROUP BY j.status
          $$;
        REVOKE ALL ON FUNCTION pv_job_stats(int) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION pv_job_stats(int) TO pv_app;
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS pv_job_stats(int)")
