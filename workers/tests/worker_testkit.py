"""Shared S3 test helpers for workers/tests.

Lives outside conftest.py: several test dirs have a conftest.py, so `from conftest import …` resolves to
whichever one pytest imported first when the whole repo runs.
"""
from typing import Any

S3_KEY, S3_SECRET, S3_BUCKET = "pvtest", "pvtest-secret-123", "pv-test-data"


def s3_test_client(endpoint: str) -> Any:
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3", endpoint_url=endpoint, aws_access_key_id=S3_KEY, aws_secret_access_key=S3_SECRET, region_name="us-east-1",
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}, retries={"max_attempts": 1}),
    )  # fmt: skip
