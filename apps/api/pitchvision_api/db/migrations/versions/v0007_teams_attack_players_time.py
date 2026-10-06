"""SCR-14 E05 / E06: attacking direction per team, on-screen time per track.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-06

teams
- attack_h1      'left' | 'right': the side of the pitch (as the main broadcast camera sees it) this team attacks
                 in the first half; NULL = unknown. Stats by thirds of the pitch need it (UIUX SCR-14 E05).
- attack_source  'auto' (S8 guessed it from where the team's goalkeepers stand) | 'user' (SCR-14) — a re-run
                 never overwrites 'user'.
players
- frames, t0_ms, t1_ms   how long / when the global track is on screen (SCR-14 E06 lists goalkeepers and referees
                         longest first; the user fixes their team / role, display-only via the manifest's
                         track_overrides).
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE teams
          ADD COLUMN attack_h1 text CHECK (attack_h1 IN ('left', 'right')),
          ADD COLUMN attack_source text CHECK (attack_source IN ('auto', 'user'));
        ALTER TABLE players
          ADD COLUMN frames int,
          ADD COLUMN t0_ms int,
          ADD COLUMN t1_ms int;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE players DROP COLUMN t1_ms, DROP COLUMN t0_ms, DROP COLUMN frames;
        ALTER TABLE teams DROP COLUMN attack_source, DROP COLUMN attack_h1;
        """
    )
