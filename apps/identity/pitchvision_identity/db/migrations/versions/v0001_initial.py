"""Initial global schema (AD §9.4): users, orgs, memberships, invites, refresh_tokens, email_tokens, oauth_accounts.

Revision ID: 0001
Revises:
Create Date: 2026-10-05

No video, position or player data lives here. Tokens (refresh, email, invite) are stored as SHA-256 hex
digests only. Status/role lists are frozen copies at this revision; change them only in a new migration.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DDL = """
CREATE TABLE users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
  email text NOT NULL UNIQUE CHECK (email = lower(btrim(email)) AND position('@' IN email) > 1),
  password_hash text,
  email_verified_at timestamptz,
  signup_plan text,
  last_active_org_id uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE orgs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
  kind text NOT NULL CHECK (kind IN ('club', 'academy', 'media', 'agency', 'individual', 'other')),
  region text NOT NULL CHECK (region IN ('eu', 'us')),
  created_by uuid REFERENCES users (id) ON DELETE SET NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

-- Data residency is chosen once (UIUX SCR-07 step 2): refuse any UPDATE of region.
CREATE FUNCTION orgs_region_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.region IS DISTINCT FROM OLD.region THEN
    RAISE EXCEPTION 'orgs.region is immutable' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER orgs_region_immutable BEFORE UPDATE OF region ON orgs
  FOR EACH ROW EXECUTE FUNCTION orgs_region_immutable();

CREATE TABLE memberships (
  org_id uuid NOT NULL REFERENCES orgs (id) ON DELETE CASCADE,
  user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
  role text NOT NULL CHECK (role IN ('owner', 'admin', 'editor', 'viewer')),
  title text,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (org_id, user_id)
);
CREATE INDEX memberships_user_id_idx ON memberships (user_id);

CREATE TABLE invites (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL REFERENCES orgs (id) ON DELETE CASCADE,
  email text NOT NULL CHECK (email = lower(btrim(email))),
  role text NOT NULL CHECK (role IN ('admin', 'editor', 'viewer')),
  token_hash text NOT NULL UNIQUE,
  invited_by uuid REFERENCES users (id) ON DELETE SET NULL,
  message text,
  expires_at timestamptz NOT NULL,
  accepted_at timestamptz,
  accepted_by uuid REFERENCES users (id) ON DELETE SET NULL,
  revoked_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX invites_org_id_idx ON invites (org_id);

CREATE TABLE refresh_tokens (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
  family_id uuid NOT NULL,
  token_hash text NOT NULL UNIQUE,
  org_id uuid,
  expires_at timestamptz NOT NULL,
  used_at timestamptz,
  revoked_at timestamptz,
  user_agent text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX refresh_tokens_family_id_idx ON refresh_tokens (family_id);
CREATE INDEX refresh_tokens_user_id_idx ON refresh_tokens (user_id);

CREATE TABLE email_tokens (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
  purpose text NOT NULL CHECK (purpose IN ('verify_email', 'reset_password')),
  token_hash text NOT NULL UNIQUE,
  expires_at timestamptz NOT NULL,
  used_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX email_tokens_user_id_idx ON email_tokens (user_id);

CREATE TABLE oauth_accounts (
  provider text NOT NULL,
  subject text NOT NULL,
  user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
  email text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (provider, subject)
);
CREATE INDEX oauth_accounts_user_id_idx ON oauth_accounts (user_id);
"""

DROP = """
DROP TABLE IF EXISTS oauth_accounts, email_tokens, refresh_tokens, invites, memberships, orgs, users CASCADE;
DROP FUNCTION IF EXISTS orgs_region_immutable();
"""


def upgrade() -> None:
    op.execute(DDL)


def downgrade() -> None:
    op.execute(DROP)
