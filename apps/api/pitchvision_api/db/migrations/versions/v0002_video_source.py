"""videos: source object + upload/ingest bookkeeping (M1-BE-07/08).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-05

- object_key    key of the source object in the region bucket (pitchvision_schemas.keys.source_key)
- upload_id     S3 multipart UploadId while status = 'uploading' (NULL once completed/aborted)
- size_bytes    declared size for uploads, real size once the object exists
- thumbnail_url poster frame for YouTube sources (from yt-dlp metadata)
- error         {code, reason, detail} when status = 'failed'
- status gains 'ingesting' (YouTube download in progress)
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUSES_0001 = "'uploading', 'ready', 'processing', 'done', 'failed', 'deleted'"
STATUSES_0002 = "'uploading', 'ingesting', 'ready', 'processing', 'done', 'failed', 'deleted'"


def upgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE videos
          ADD COLUMN object_key text,
          ADD COLUMN upload_id text,
          ADD COLUMN size_bytes bigint CHECK (size_bytes >= 0),
          ADD COLUMN thumbnail_url text,
          ADD COLUMN error jsonb,
          DROP CONSTRAINT videos_status_check,
          ADD CONSTRAINT videos_status_check CHECK (status IN ({STATUSES_0002}));
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        UPDATE videos SET status = 'failed' WHERE status = 'ingesting';
        ALTER TABLE videos
          DROP CONSTRAINT videos_status_check,
          ADD CONSTRAINT videos_status_check CHECK (status IN ({STATUSES_0001})),
          DROP COLUMN error,
          DROP COLUMN thumbnail_url,
          DROP COLUMN size_bytes,
          DROP COLUMN upload_id,
          DROP COLUMN object_key;
        """
    )
