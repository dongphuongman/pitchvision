"""pv_worker role: the pipeline's DB login, the only role that may write `positions` (M1-BE-14).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-06

Why: `positions` is a compressed Timescale hypertable, which cannot have row security (see 0001), so pv_app — the
API's role — has no privileges on it and reads through the security-barrier view `positions_org`. S8 load-db must
write it, and the API must still not be able to. The tenant story stays "every write is scoped to app.org_id":

- pv_worker: NOLOGIN here (deployments grant LOGIN + password out of band, like pv_app), NOBYPASSRLS, member of
  pv_app — so it has exactly the API's RLS-bound table grants (videos, jobs, job_tasks, shots, teams, players, …,
  and the 0003 SECURITY DEFINER helpers) — plus INSERT on positions. Nothing else; in particular no UPDATE/DELETE
  and no SELECT on positions (it reads through positions_org like the API).
- positions_org_guard: BEFORE INSERT row trigger on positions that rejects any row whose org_id is not the
  transaction's app.org_id (SQLSTATE 42501). This is the write-side twin of positions_org: a worker bug cannot
  insert rows into another tenant even though the table has no RLS. COPY fires it too. Superusers skip it, as they
  skip RLS.
- pv_delete_positions(video): SECURITY DEFINER, deletes the video's rows of the CURRENT org only (app.org_id) and
  returns the count — the only delete path (S8 idempotency: a re-run replaces the video's rows; erasure).

Granting pv_worker to pv_app is NOT done (membership goes one way): the API cannot SET ROLE pv_worker.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "pv_app"
WORKER_ROLE = "pv_worker"
ORG_GUC = "NULLIF(current_setting('app.org_id', true), '')::uuid"


def upgrade() -> None:
    op.execute(
        f"""
        DO $$ BEGIN
          IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{WORKER_ROLE}') THEN
            CREATE ROLE {WORKER_ROLE} NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
          END IF;
        END $$;
        GRANT {APP_ROLE} TO {WORKER_ROLE};
        GRANT USAGE ON SCHEMA public TO {WORKER_ROLE};
        GRANT INSERT ON positions TO {WORKER_ROLE};

        CREATE FUNCTION pv_positions_org_guard() RETURNS trigger
          LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
          BEGIN
            -- superusers skip the check like they skip RLS (manual ops, the migration role's own tests)
            IF current_setting('is_superuser') <> 'on' AND NEW.org_id IS DISTINCT FROM {ORG_GUC} THEN
              RAISE EXCEPTION 'positions.org_id % does not match app.org_id', NEW.org_id USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
          END $$;
        CREATE TRIGGER positions_org_guard BEFORE INSERT ON positions
          FOR EACH ROW EXECUTE FUNCTION pv_positions_org_guard();

        CREATE FUNCTION pv_delete_positions(p_video uuid) RETURNS bigint
          LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
            WITH d AS (DELETE FROM public.positions
                        WHERE video_id = p_video AND org_id = {ORG_GUC} RETURNING 1)
            SELECT count(*) FROM d
          $$;
        REVOKE ALL ON FUNCTION pv_delete_positions(uuid) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION pv_delete_positions(uuid) TO {WORKER_ROLE};
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        DROP FUNCTION IF EXISTS pv_delete_positions(uuid);
        DROP TRIGGER IF EXISTS positions_org_guard ON positions;
        DROP FUNCTION IF EXISTS pv_positions_org_guard();
        DO $$ BEGIN
          IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{WORKER_ROLE}') THEN
            REVOKE ALL ON positions FROM {WORKER_ROLE};
            REVOKE ALL ON SCHEMA public FROM {WORKER_ROLE};
            REVOKE {APP_ROLE} FROM {WORKER_ROLE};
            DROP ROLE {WORKER_ROLE};
          END IF;
        EXCEPTION WHEN dependent_objects_still_exist THEN
          RAISE NOTICE 'role {WORKER_ROLE} still owns privileges elsewhere in the cluster; kept';
        END $$;
        """
    )
