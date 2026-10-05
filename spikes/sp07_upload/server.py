"""SP-07 — minimal upload API: S3 multipart with presigned part URLs (R2 / SeaweedFS / any S3).

The browser PUTs parts straight to object storage; the API only signs and completes.
    S3_ENDPOINT=http://127.0.0.1:8333 python server.py 8767
"""
import json
import os
import sys
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import boto3
from botocore.config import Config

ENV = Path(__file__).parent / ".env"  # optional KEY=VALUE file (R2 credentials); real env vars win
if ENV.exists():
    for line in ENV.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
BUCKET = os.environ.get("S3_BUCKET", "pv-uploads")
s3 = boto3.client(
    "s3", endpoint_url=os.environ.get("S3_ENDPOINT", "http://127.0.0.1:8333"),
    aws_access_key_id=os.environ.get("S3_KEY", "pvspike"), aws_secret_access_key=os.environ.get("S3_SECRET", "pvspike-secret-123"),
    region_name="auto", config=Config(signature_version="s3v4", s3={"addressing_style": "path"}))
MIN_PART = 5 * 1024 * 1024  # S3/R2 minimum for every part but the last; R2 also wants equal-sized parts
URL_TTL = 3600


def setup_bucket() -> None:
    if "r2.cloudflarestorage.com" in os.environ.get("S3_ENDPOINT", ""):
        return  # R2: bucket + CORS are configured in the dashboard; the object-scoped token cannot change them
    try:
        s3.create_bucket(Bucket=BUCKET)
    except s3.exceptions.BucketAlreadyOwnedByYou:
        pass
    except Exception as e:  # noqa: BLE001 — SeaweedFS answers differently on re-create
        if "BucketAlready" not in str(e):
            raise
    # The browser must be able to read ETag from each part response, or it cannot complete the upload.
    s3.put_bucket_cors(Bucket=BUCKET, CORSConfiguration={"CORSRules": [{
        "AllowedOrigins": ["http://127.0.0.1:8767", "http://localhost:8767"], "AllowedMethods": ["PUT", "GET", "HEAD"],
        "AllowedHeaders": ["*"], "ExposeHeaders": ["ETag"], "MaxAgeSeconds": 3600}]})


def sign_parts(key: str, upload_id: str, numbers: list[int]) -> dict[int, str]:
    return {n: s3.generate_presigned_url("upload_part", Params={"Bucket": BUCKET, "Key": key, "UploadId": upload_id, "PartNumber": n},
                                         ExpiresIn=URL_TTL) for n in numbers}


class Handler(SimpleHTTPRequestHandler):
    def _json(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def _body(self) -> dict:
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/api/uploads/parts":  # resume: which parts does storage already have?
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            parts, marker = [], 0
            while True:
                r = s3.list_parts(Bucket=BUCKET, Key=q["key"], UploadId=q["uploadId"], PartNumberMarker=marker)
                parts += [{"PartNumber": p["PartNumber"], "ETag": p["ETag"], "Size": p["Size"]} for p in r.get("Parts", [])]
                if not r.get("IsTruncated"):
                    break
                marker = r["NextPartNumberMarker"]
            return self._json(200, {"parts": parts})
        return super().do_GET()

    def do_POST(self):
        b, p = self._body(), urlparse(self.path).path
        try:
            if p == "/api/uploads":
                size, part = int(b["size"]), max(int(b.get("partSize", 16 << 20)), MIN_PART)
                key = f"uploads/{uuid.uuid4().hex}/{Path(b['name']).name}"
                up = s3.create_multipart_upload(Bucket=BUCKET, Key=key, ContentType=b.get("type") or "video/mp4")
                return self._json(200, {"key": key, "uploadId": up["UploadId"], "partSize": part, "parts": -(-size // part)})
            if p == "/api/uploads/sign":  # signed in batches so URLs never outlive a slow upload
                return self._json(200, {"urls": sign_parts(b["key"], b["uploadId"], b["numbers"])})
            if p == "/api/uploads/complete":
                parts = sorted(b["parts"], key=lambda x: x["PartNumber"])
                s3.complete_multipart_upload(Bucket=BUCKET, Key=b["key"], UploadId=b["uploadId"], MultipartUpload={"Parts": parts})
                h = s3.head_object(Bucket=BUCKET, Key=b["key"])
                return self._json(200, {"size": h["ContentLength"], "etag": h["ETag"]})
            if p == "/api/uploads/abort":
                s3.abort_multipart_upload(Bucket=BUCKET, Key=b["key"], UploadId=b["uploadId"])
                return self._json(200, {})
        except Exception as e:  # noqa: BLE001 — spike: surface storage errors to the page
            return self._json(500, {"error": str(e)})
        self._json(404, {"error": "not found"})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    os.chdir(Path(__file__).parent)
    setup_bucket()
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 8767), Handler).serve_forever()
