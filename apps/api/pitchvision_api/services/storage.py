"""S3-compatible object storage (Cloudflare R2 in cloud, SeaweedFS locally) — AD §8.1, §9.2, §9.5.

Two boto3 clients, same credentials:
- `internal` talks to PV_STORAGE_ENDPOINT (server-side calls: create/list/complete/abort/head/delete);
- `presigner` only signs URLs for PV_STORAGE_PUBLIC_ENDPOINT — the host the BROWSER reaches. SigV4 signs the
  Host header, so a URL signed for `http://storage:8333` is useless to a browser on `localhost:8333`.
  On R2 both are the account's S3 endpoint (presigned URLs don't work through a custom domain).

boto3 is blocking: async callers wrap these methods in `asyncio.to_thread`.
"""

import logging
from dataclasses import dataclass
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from pitchvision_schemas import ErrorCode, keys

from pitchvision_api.errors import ApiError
from pitchvision_api.settings import Settings

log = logging.getLogger(__name__)

PART_SIZE = 16 * 1024 * 1024  # fixed: R2 requires equal-sized parts (all but the last), AD §8.1
MAX_PARTS = 10_000  # S3/R2 multipart limit


@dataclass(frozen=True)
class StoredObject:
    body: Any  # botocore StreamingBody (blocking .read(n) / .close())
    size: int
    etag: str | None


@dataclass(frozen=True)
class StoredPart:
    part_number: int
    etag: str
    size: int


def source_key(org_id: object, video_id: object, ext: str) -> str:
    """AD §9.2 layout (storage class first, for R2 lifecycle rules) — see pitchvision_schemas.keys."""
    return keys.source_key(str(org_id), str(video_id), ext)


def part_count(size: int, part_size: int = PART_SIZE) -> int:
    return max(1, -(-size // part_size))


def expected_part_size(part_number: int, size: int, part_size: int = PART_SIZE) -> int:
    n = part_count(size, part_size)
    return part_size if part_number < n else size - (n - 1) * part_size


def normalize_etag(etag: str) -> str:
    return etag.strip().strip('"')


def is_missing(e: ClientError) -> bool:
    code = str(e.response.get("Error", {}).get("Code", ""))
    return code in {"NoSuchUpload", "NoSuchKey", "404", "NotFound"}


def storage_error(e: Exception) -> ApiError:
    log.warning("storage call failed: %r", e)
    return ApiError(ErrorCode.INTERNAL, 502, "Object storage error", "The storage backend rejected the request.")


class Storage:
    def __init__(self, internal: Any, presigner: Any, bucket: str, presign_ttl_s: int) -> None:
        self.internal, self.presigner, self.bucket, self.presign_ttl_s = internal, presigner, bucket, presign_ttl_s

    @classmethod
    def from_settings(cls, s: Settings) -> "Storage":
        cfg = Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},  # SeaweedFS / localhost have no virtual-host DNS; R2 accepts both
            connect_timeout=5,
            read_timeout=60,
            retries={"max_attempts": 3, "mode": "standard"},
        )

        def client(endpoint: str | None) -> Any:
            return boto3.client(
                "s3",
                endpoint_url=endpoint,
                region_name=s.storage_region,
                aws_access_key_id=s.storage_access_key,
                aws_secret_access_key=s.storage_secret_key,
                config=cfg,
            )

        public = s.storage_public_endpoint or s.storage_endpoint
        return cls(client(s.storage_endpoint), client(public), s.storage_bucket, s.presign_ttl_s)

    # ---- multipart (browser uploads) ----
    def create_multipart(self, key: str, content_type: str) -> str:
        r = self.internal.create_multipart_upload(Bucket=self.bucket, Key=key, ContentType=content_type)
        return str(r["UploadId"])

    def presign_parts(self, key: str, upload_id: str, part_numbers: list[int]) -> dict[int, str]:
        """Pure local signing (no network)."""
        return {
            n: self.presigner.generate_presigned_url(
                "upload_part",
                Params={"Bucket": self.bucket, "Key": key, "UploadId": upload_id, "PartNumber": n},
                ExpiresIn=self.presign_ttl_s,
            )
            for n in part_numbers
        }

    def list_parts(self, key: str, upload_id: str) -> list[StoredPart]:
        parts: list[StoredPart] = []
        marker = 0
        while True:
            r = self.internal.list_parts(Bucket=self.bucket, Key=key, UploadId=upload_id, PartNumberMarker=marker)
            parts += [
                StoredPart(int(p["PartNumber"]), normalize_etag(p["ETag"]), int(p["Size"])) for p in r.get("Parts", [])
            ]
            if not r.get("IsTruncated"):
                return parts
            marker = int(r["NextPartNumberMarker"])

    def complete_multipart(self, key: str, upload_id: str, parts: list[tuple[int, str]]) -> None:
        self.internal.complete_multipart_upload(
            Bucket=self.bucket,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={"Parts": [{"PartNumber": n, "ETag": f'"{normalize_etag(e)}"'} for n, e in parts]},
        )

    def abort_multipart(self, key: str, upload_id: str) -> None:
        try:
            self.internal.abort_multipart_upload(Bucket=self.bucket, Key=key, UploadId=upload_id)
        except ClientError as e:
            if not is_missing(e):
                raise

    # ---- objects ----
    def head_size(self, key: str) -> int | None:
        try:
            return int(self.internal.head_object(Bucket=self.bucket, Key=key)["ContentLength"])
        except ClientError as e:
            if is_missing(e):
                return None
            raise

    def delete_object(self, key: str) -> None:
        self.internal.delete_object(Bucket=self.bucket, Key=key)

    def open_object(self, key: str) -> StoredObject | None:
        """Streaming GET (None when the key doesn't exist). The caller must close ``body``."""
        try:
            r = self.internal.get_object(Bucket=self.bucket, Key=key)
        except ClientError as e:
            if is_missing(e):
                return None
            raise
        return StoredObject(r["Body"], int(r.get("ContentLength") or 0), r.get("ETag"))

    def get_bytes(self, key: str) -> bytes | None:
        obj = self.open_object(key)
        if obj is None:
            return None
        try:
            data: bytes = obj.body.read()
            return data
        finally:
            obj.body.close()

    def presign_get(self, key: str) -> str:
        return str(
            self.presigner.generate_presigned_url(
                "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=self.presign_ttl_s
            )
        )


STORAGE_ERRORS = (ClientError, BotoCoreError)
