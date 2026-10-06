"""Server-side object storage for workers (internal PV_STORAGE_ENDPOINT; R2 / SeaweedFS speak S3)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pitchvision_schemas import keys

from pitchvision_worker.config import WorkerConfig

PART_SIZE = 16 * 1024 * 1024  # equal-sized parts: R2 rejects multipart uploads with mixed part sizes


def source_key(org_id: object, video_id: object, ext: str = "mp4") -> str:
    """AD §9.2 — must match pitchvision_api.services.storage.source_key."""
    return keys.source_key(str(org_id), str(video_id), ext)


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


class ObjectStore:
    """The few object operations the pipeline needs (stages never see S3: the task layer moves files)."""

    def __init__(self, client: Any, bucket: str) -> None:
        self.client, self.bucket = client, bucket

    @classmethod
    def from_config(cls, cfg: WorkerConfig) -> ObjectStore:
        return cls(s3_client(cfg), cfg.storage_bucket)

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as e:
            if str(e.response.get("Error", {}).get("Code")) in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def get_bytes(self, key: str) -> bytes:
        body: bytes = self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        return body

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/json") -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def download(self, key: str, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, key, str(path))
        return path

    def upload(self, key: str, path: Path) -> int:
        return upload_file(self.client, self.bucket, key, path, content_type(path))

    def copy(self, src: str, dst: str) -> None:
        self.client.copy_object(Bucket=self.bucket, Key=dst, CopySource={"Bucket": self.bucket, "Key": src})


_TYPES = {".mp4": "video/mp4", ".json": "application/json", ".parquet": "application/vnd.apache.parquet",
          ".jpg": "image/jpeg", ".png": "image/png"}  # fmt: skip


def content_type(path: Path) -> str:
    return _TYPES.get(path.suffix.lower(), "application/octet-stream")
