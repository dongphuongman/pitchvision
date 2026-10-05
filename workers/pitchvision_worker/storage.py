"""Server-side object storage for workers (internal PV_STORAGE_ENDPOINT; R2 / SeaweedFS speak S3)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pitchvision_worker.config import WorkerConfig

PART_SIZE = 16 * 1024 * 1024  # equal-sized parts: R2 rejects multipart uploads with mixed part sizes


def source_key(org_id: object, video_id: object, ext: str = "mp4") -> str:
    """AD §9.2 — must match pitchvision_api.services.storage.source_key."""
    return f"orgs/{org_id}/videos/{video_id}/source/original.{ext}"


def s3_client(cfg: WorkerConfig) -> Any:
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=cfg.storage_endpoint,
        region_name=cfg.storage_region,
        aws_access_key_id=cfg.storage_access_key,
        aws_secret_access_key=cfg.storage_secret_key,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=10,
            read_timeout=120,
            retries={"max_attempts": 5, "mode": "standard"},
        ),
    )


def upload_file(client: Any, bucket: str, key: str, path: Path, content_type: str = "video/mp4") -> int:
    """Multipart upload with fixed 16 MiB parts; returns the stored size (HEAD)."""
    from boto3.s3.transfer import TransferConfig

    cfg = TransferConfig(multipart_threshold=PART_SIZE, multipart_chunksize=PART_SIZE, max_concurrency=4)
    client.upload_file(str(path), bucket, key, ExtraArgs={"ContentType": content_type}, Config=cfg)
    return int(client.head_object(Bucket=bucket, Key=key)["ContentLength"])
