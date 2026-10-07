"""saved_teams (SCR-40 O03, SCR-14 E02 suggestions) and the member_joined notification type (SCR-51).

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-07

- saved_teams     the org's team names + display colours, upserted when an editor confirms teams (SCR-14 E09) with a
                  name other than the defaults; unique per org on lower(name). ``uses`` / ``last_used_at`` order the
                  SCR-14 suggestions. Admins rename / recolour / delete them (O03); videos keep their own copies.
- notifications.type += member_joined (identity's member_events, pulled by the worker; in-app only).
- pv_purge_org (0008) re-created with saved_teams.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "pv_app"
ORG_GUC = "NULLIF(current_setting('app.org_id', true), '')::uuid"
OLD_TYPES = ("'job_ready', 'needs_teams', 'job_failed', 'export_ready', 'export_failed', 'quota_80', 'quota_100', "
             "'data_export_ready'")  # fmt: skip
TYPES = OLD_TYPES + ", 'member_joined'"
# 0008's pv_purge_org list + saved_teams (an erased org loses them too)
OLD_ORG_TABLES = ("notifications", "data_exports", "exports", "idempotency_keys", "reports", "tactics",
                  "stats_team", "stats_player", "shots", "corrections", "players", "teams", "job_tasks", "jobs",
                  "videos")  # fmt: skip
ORG_TABLES = ("saved_teams", *OLD_ORG_TABLES)


def upgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE notifications DROP CONSTRAINT notifications_type_check;
        ALTER TABLE notifications ADD CONSTRAINT notifications_type_check CHECK (type IN ({TYPES}));

        CREATE TABLE saved_teams (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id uuid NOT NULL,
          name text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 40),
          color text NOT NULL CHECK (color ~ '^#[0-9a-f]{{6}}$'),
          uses int NOT NULL DEFAULT 1,
          created_at timestamptz NOT NULL DEFAULT now(),
          last_used_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX uq_saved_teams_name ON saved_teams (org_id, lower(name));
        GRANT SELECT, INSERT, UPDATE, DELETE ON saved_teams TO {APP_ROLE};
        ALTER TABLE saved_teams ENABLE ROW LEVEL SECURITY;
        ALTER TABLE saved_teams FORCE ROW LEVEL SECURITY;
        CREATE POLICY org_isolation ON saved_teams USING (org_id = {ORG_GUC}) WITH CHECK (org_id = {ORG_GUC});
        """
    )
    op.execute(_purge_org(ORG_TABLES))


def _purge_org(tables: tuple[str, ...]) -> str:
    body = "\n".join(
        f"  DELETE FROM public.{t} WHERE org_id = p_org; GET DIAGNOSTICS c = ROW_COUNT; n := n + c;" for t in tables
    )
    return f"""
        CREATE OR REPLACE FUNCTION pv_purge_org(p_org uuid) RETURNS bigint
          LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
        DECLARE n bigint := 0; c bigint;
        BEGIN
          DELETE FROM public.positions WHERE org_id = p_org; GET DIAGNOSTICS c = ROW_COUNT; n := n + c;
        {body}
          RETURN n;
        END $$;
        """


def downgrade() -> None:
    op.execute(
        f"""
        DROP TABLE IF EXISTS saved_teams;
        DELETE FROM notifications WHERE type = 'member_joined';
        ALTER TABLE notifications DROP CONSTRAINT notifications_type_check;
        ALTER TABLE notifications ADD CONSTRAINT notifications_type_check CHECK (type IN ({OLD_TYPES}));
        """
    )
    op.execute(_purge_org(OLD_ORG_TABLES))
