"""Initial regional schema (AD §9.3) + shots + idempotency_keys, tenant RLS, app role pv_app.

Revision ID: 0001
Revises:
Create Date: 2026-10-05

Every tenant table carries org_id and an `org_isolation` policy keyed on the `app.org_id` GUC, which the
API sets per transaction (SET LOCAL). Children of videos reference (video_id, org_id) so a row can never
point at another org's video — FK checks bypass RLS, the composite key closes that gap.

positions is the exception: TimescaleDB refuses columnstore compression on a table with row security
("columnstore cannot be used on table with row security"), and AD §9.3 needs compression. So pv_app gets
no privileges on positions; it reads through the security-barrier view `positions_org`, filtered on the
same GUC. Writes come from workers (their own role, later PR).

Status lists are frozen copies of the enums at this revision; change them only in a new migration.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "pv_app"

RLS_TABLES = (
    "videos",
    "jobs",
    "job_tasks",
    "teams",
    "players",
    "corrections",
    "stats_player",
    "stats_team",
    "tactics",
    "reports",
    "shots",
    "idempotency_keys",
)

ORG_GUC = "NULLIF(current_setting('app.org_id', true), '')::uuid"

VIDEO_FK = "FOREIGN KEY (video_id, org_id) REFERENCES videos (id, org_id) ON DELETE CASCADE"

DDL = f"""
CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE videos (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL,
  created_by uuid NOT NULL,
  source_type text CHECK (source_type IN ('upload', 'youtube', 'rtsp', 'import')),
  source_uri text,
  title text,
  duration_ms int,
  fps numeric(6, 3),
  width int,
  height int,
  status text NOT NULL DEFAULT 'uploading'
    CHECK (status IN ('uploading', 'ready', 'processing', 'done', 'failed', 'deleted')),
  revision int NOT NULL DEFAULT 0,
  rights_confirmed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  deleted_at timestamptz,
  CONSTRAINT uq_videos_id_org UNIQUE (id, org_id)
);
CREATE INDEX ix_videos_org_created ON videos (org_id, created_at, id);

CREATE TABLE jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  video_id uuid NOT NULL,
  org_id uuid NOT NULL,
  image_tag text NOT NULL,
  config jsonb NOT NULL,
  status text NOT NULL DEFAULT 'queued'
    CHECK (status IN ('queued', 'ingesting', 'calibrating', 'inferring', 'postprocessing',
                      'done', 'failed', 'cancelled')),
  progress numeric(5, 2) NOT NULL DEFAULT 0,
  fps_agg numeric,
  error jsonb,
  cost_usd numeric(8, 4),
  created_at timestamptz NOT NULL DEFAULT now(),
  started_at timestamptz,
  finished_at timestamptz,
  CONSTRAINT uq_jobs_id_org UNIQUE (id, org_id),
  {VIDEO_FK}
);
CREATE INDEX ix_jobs_video ON jobs (video_id);
CREATE INDEX ix_jobs_org_status ON jobs (org_id, status);

CREATE TABLE job_tasks (
  job_id uuid NOT NULL,
  org_id uuid NOT NULL,
  task_key text NOT NULL,
  backend text,
  external_id text,
  status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'failed')),
  attempts int NOT NULL DEFAULT 0,
  error text,
  started_at timestamptz,
  finished_at timestamptz,
  PRIMARY KEY (job_id, task_key),
  FOREIGN KEY (job_id, org_id) REFERENCES jobs (id, org_id) ON DELETE CASCADE
);

CREATE TABLE teams (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  video_id uuid NOT NULL,
  org_id uuid NOT NULL,
  cluster int,
  name text,
  color text,
  confirmed boolean NOT NULL DEFAULT false,
  CONSTRAINT uq_teams_id_org UNIQUE (id, org_id),
  {VIDEO_FK}
);
CREATE INDEX ix_teams_video ON teams (video_id);

CREATE TABLE players (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  video_id uuid NOT NULL,
  org_id uuid NOT NULL,
  track_id int NOT NULL,
  team_id uuid,
  role text,
  jersey int,
  jersey_conf real,
  name text,
  confirmed boolean NOT NULL DEFAULT false,
  CONSTRAINT uq_players_video_track UNIQUE (video_id, track_id),
  {VIDEO_FK},
  FOREIGN KEY (team_id, org_id) REFERENCES teams (id, org_id) ON DELETE SET NULL (team_id)
);

-- 5 Hz positions; no FK to videos (bulk-insert path, compressed chunks) — rows are purged with the video.
CREATE TABLE positions (
  video_id uuid NOT NULL,
  org_id uuid NOT NULL,
  ts_ms int NOT NULL,
  track_id int NOT NULL,
  team smallint,
  px real,
  py real,
  vx real,
  vy real,
  q real
);
SELECT create_hypertable('positions', by_range('ts_ms', 600000));
CREATE INDEX ix_positions_video_ts ON positions (video_id, ts_ms);
ALTER TABLE positions SET (timescaledb.compress, timescaledb.compress_segmentby = 'video_id, track_id');
CREATE VIEW positions_org WITH (security_barrier) AS
  SELECT * FROM positions WHERE org_id = {ORG_GUC};

CREATE TABLE corrections (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  video_id uuid NOT NULL,
  org_id uuid NOT NULL,
  revision int NOT NULL,
  type text NOT NULL,
  payload jsonb NOT NULL,
  created_by uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  {VIDEO_FK}
);
CREATE INDEX ix_corrections_video_revision ON corrections (video_id, revision);

CREATE TABLE stats_player (
  video_id uuid NOT NULL,
  org_id uuid NOT NULL,
  player_id uuid NOT NULL,
  revision int NOT NULL DEFAULT 0,
  metrics jsonb NOT NULL,
  PRIMARY KEY (video_id, player_id),
  {VIDEO_FK}
);

CREATE TABLE stats_team (
  video_id uuid NOT NULL,
  org_id uuid NOT NULL,
  team_id uuid NOT NULL,
  revision int NOT NULL DEFAULT 0,
  metrics jsonb NOT NULL,
  PRIMARY KEY (video_id, team_id),
  {VIDEO_FK}
);

CREATE TABLE tactics (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  video_id uuid NOT NULL,
  org_id uuid NOT NULL,
  revision int NOT NULL DEFAULT 0,
  kind text NOT NULL,
  data jsonb NOT NULL,
  {VIDEO_FK}
);
CREATE INDEX ix_tactics_video_revision_kind ON tactics (video_id, revision, kind);

CREATE TABLE reports (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  video_id uuid NOT NULL,
  org_id uuid NOT NULL,
  lang text NOT NULL,
  content_md text,
  model text,
  facts_checked boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  {VIDEO_FK}
);
CREATE INDEX ix_reports_video ON reports (video_id);

CREATE TABLE shots (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  video_id uuid NOT NULL,
  org_id uuid NOT NULL,
  t0_ms int NOT NULL CHECK (t0_ms >= 0),
  t1_ms int NOT NULL,
  type text NOT NULL CHECK (type IN ('main_wide', 'close_up', 'replay', 'other')),
  conf real NOT NULL CHECK (conf >= 0 AND conf <= 1),
  CONSTRAINT ck_shots_ordered CHECK (t1_ms > t0_ms),
  {VIDEO_FK}
);
CREATE INDEX ix_shots_video_t0 ON shots (video_id, t0_ms);

-- Idempotency-Key store (AD §10.1); rows older than 24 h are treated as absent and overwritten.
CREATE TABLE idempotency_keys (
  org_id uuid NOT NULL,
  key text NOT NULL CHECK (char_length(key) BETWEEN 1 AND 255),
  method text NOT NULL,
  path text NOT NULL,
  request_hash text NOT NULL,
  response_status int,
  response_body jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (org_id, key)
);
CREATE INDEX ix_idempotency_keys_created ON idempotency_keys (created_at);
"""


def upgrade() -> None:
    op.execute(DDL)
    op.execute(
        f"""
        DO $$ BEGIN
          IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
            CREATE ROLE {APP_ROLE} NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
          END IF;
        END $$;
        GRANT USAGE ON SCHEMA public TO {APP_ROLE};
        GRANT SELECT ON positions_org TO {APP_ROLE};
        """
    )
    for table in RLS_TABLES:
        op.execute(
            f"""
            GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {APP_ROLE};
            ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY org_isolation ON {table} USING (org_id = {ORG_GUC}) WITH CHECK (org_id = {ORG_GUC});
            """
        )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS positions_org")
    for table in (*reversed(RLS_TABLES), "positions"):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute(
        f"""
        DO $$ BEGIN
          IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
            REVOKE ALL ON SCHEMA public FROM {APP_ROLE};
            DROP ROLE {APP_ROLE};
          END IF;
        EXCEPTION WHEN dependent_objects_still_exist THEN
          RAISE NOTICE 'role {APP_ROLE} still owns privileges elsewhere in the cluster; kept';
        END $$;
        """
    )
