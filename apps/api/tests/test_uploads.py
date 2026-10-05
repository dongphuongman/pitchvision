"""M1-BE-07: presigned multipart uploads against a real S3 (SeaweedFS) + real Postgres (RLS)."""
import hashlib
import os
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from pitchvision_api.main import create_app
from pitchvision_api.services.storage import PART_SIZE, source_key
from pitchvision_api.services.uploads import container_of
from pitchvision_api.settings import Settings

from .conftest import S3, Db, s3_client
from .test_app import assert_problem

MIB = 1024 * 1024


@pytest.fixture
async def client(migrated: Db, s3: S3) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(
        env="local",
        dev_auth=True,
        database_url=migrated.app_url,
        storage_endpoint=s3.endpoint,
        storage_bucket=s3.bucket,
        storage_access_key=s3.access_key,
        storage_secret_key=s3.secret_key,
        storage_region="us-east-1",
        max_upload_bytes=200 * MIB,
    )
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await app.state.engine.dispose()


def who(org: uuid.UUID, role: str = "editor") -> dict[str, str]:
    return {"X-Dev-Org": str(org), "X-Dev-Role": role}


async def put_part(url: str, data: bytes) -> str:
    async with httpx.AsyncClient(timeout=60) as raw:
        r = await raw.put(url, content=data)
    assert r.status_code == 200, r.text
    return r.headers["etag"]


async def start(c: httpx.AsyncClient, org: uuid.UUID, size: int, name: str = "match.mp4") -> dict[str, object]:
    r = await c.post("/api/v1/videos/uploads", json={"filename": name, "size": size, "mime": "video/mp4"},
                     headers=who(org))  # fmt: skip
    assert r.status_code == 201, r.text
    body: dict[str, object] = r.json()
    return body


async def test_full_round_trip(client: httpx.AsyncClient, s3: S3) -> None:
    org = uuid.uuid4()
    data = os.urandom(2 * PART_SIZE + 12345)
    up = await start(client, org, len(data), "Final 2026.MP4")
    vid = up["video_id"]
    assert up["part_size"] == PART_SIZE == 16 * MIB and up["part_count"] == 3
    assert [p["part_number"] for p in up["parts"]] == [1, 2, 3]  # type: ignore[attr-defined]
    assert up["video"]["status"] == "uploading" and up["video"]["title"] == "Final 2026"  # type: ignore[index]
    assert up["url_expires_in"] == 900

    chunks = [data[i : i + PART_SIZE] for i in range(0, len(data), PART_SIZE)]
    urls = {p["part_number"]: p["url"] for p in up["parts"]}  # type: ignore[attr-defined]
    etags = {1: await put_part(urls[1], chunks[0])}

    # resume: storage knows part 1; re-sign the rest
    r = await client.get(f"/api/v1/videos/{vid}/uploads", headers=who(org))
    assert r.status_code == 200
    assert [(p["part_number"], p["size"]) for p in r.json()["parts"]] == [(1, PART_SIZE)]
    r = await client.post(f"/api/v1/videos/{vid}/uploads/sign", json={"part_numbers": [3, 2, 2]}, headers=who(org))
    assert r.status_code == 200
    signed = {p["part_number"]: p["url"] for p in r.json()["parts"]}
    assert sorted(signed) == [2, 3]
    for n in (2, 3):
        etags[n] = await put_part(signed[n], chunks[n - 1])
    assert_problem(
        await client.post(f"/api/v1/videos/{vid}/uploads/sign", json={"part_numbers": [4]}, headers=who(org)),
        422, "VALIDATION_FAILED",
    )  # fmt: skip

    parts = [{"part_number": n, "etag": e} for n, e in etags.items()]
    bad = [*parts[:2], {"part_number": 3, "etag": '"deadbeef"'}]
    assert_problem(await client.post(f"/api/v1/videos/{vid}/uploads/complete", json={"parts": bad}, headers=who(org)),
                   409, "CONFLICT")  # fmt: skip
    assert_problem(
        await client.post(f"/api/v1/videos/{vid}/uploads/complete", json={"parts": parts[:2]}, headers=who(org)),
        422, "VALIDATION_FAILED",
    )  # fmt: skip
    r = await client.post(f"/api/v1/videos/{vid}/uploads/complete", json={"parts": parts}, headers=who(org))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ready" and r.json()["size_bytes"] == len(data)
    # idempotent retry of complete
    r2 = await client.post(f"/api/v1/videos/{vid}/uploads/complete", json={"parts": parts}, headers=who(org))
    assert r2.status_code == 200 and r2.json()["status"] == "ready"

    key = source_key(org, vid, "mp4")
    obj = s3_client(s3.endpoint).get_object(Bucket=s3.bucket, Key=key)
    body = obj["Body"].read()
    assert obj["ContentType"] == "video/mp4"
    assert hashlib.md5(body).digest() == hashlib.md5(data).digest()

    r = await client.get(f"/api/v1/videos/{vid}", headers=who(org, "viewer"))
    assert r.status_code == 200 and r.json()["status"] == "ready"
    # no more upload ops on a ready video
    assert_problem(await client.get(f"/api/v1/videos/{vid}/uploads", headers=who(org)), 409, "CONFLICT")


async def test_unequal_parts_rejected(client: httpx.AsyncClient) -> None:
    """R2 requires equal-sized parts; we enforce it everywhere so SeaweedFS/MinIO behave the same."""
    org = uuid.uuid4()
    size = 2 * PART_SIZE
    up = await start(client, org, size)
    urls = {p["part_number"]: p["url"] for p in up["parts"]}  # type: ignore[attr-defined]
    small = 6 * MIB  # valid S3 part (≥ 5 MiB) but not 16 MiB
    e1 = await put_part(urls[1], os.urandom(small))
    e2 = await put_part(urls[2], os.urandom(size - small))
    parts = [{"part_number": 1, "etag": e1}, {"part_number": 2, "etag": e2}]
    r = await client.post(f"/api/v1/videos/{up['video_id']}/uploads/complete", json={"parts": parts}, headers=who(org))
    body = assert_problem(r, 409, "CONFLICT")
    assert f"part 1 is {small} bytes, expected {PART_SIZE}" in str(body["detail"])


async def test_rls_and_roles(client: httpx.AsyncClient) -> None:
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    up = await start(client, org_a, 1000)
    vid = up["video_id"]
    assert (await client.get(f"/api/v1/videos/{vid}", headers=who(org_a))).status_code == 200
    assert_problem(await client.get(f"/api/v1/videos/{vid}", headers=who(org_b)), 404, "NOT_FOUND")
    assert_problem(await client.get(f"/api/v1/videos/{vid}/uploads", headers=who(org_b)), 404, "NOT_FOUND")
    assert_problem(await client.delete(f"/api/v1/videos/{vid}/uploads", headers=who(org_b)), 404, "NOT_FOUND")
    assert (await client.get("/api/v1/videos", headers=who(org_b))).json() == {"items": [], "next_cursor": None}
    # viewers read, editors write
    r = await client.post("/api/v1/videos/uploads", json={"filename": "a.mp4", "size": 10, "mime": "video/mp4"},
                          headers=who(org_a, "viewer"))  # fmt: skip
    assert_problem(r, 403, "FORBIDDEN")
    assert (await client.get("/api/v1/videos", headers=who(org_a, "viewer"))).status_code == 200


async def test_abort_marks_deleted(client: httpx.AsyncClient, s3: S3) -> None:
    org = uuid.uuid4()
    up = await start(client, org, 3 * MIB)
    vid = up["video_id"]
    await put_part(up["parts"][0]["url"], os.urandom(3 * MIB))  # type: ignore[index]
    assert (await client.delete(f"/api/v1/videos/{vid}/uploads", headers=who(org))).status_code == 204
    assert_problem(await client.get(f"/api/v1/videos/{vid}", headers=who(org)), 404, "NOT_FOUND")
    assert (await client.get("/api/v1/videos", headers=who(org))).json()["items"] == []
    uploads = s3_client(s3.endpoint).list_multipart_uploads(Bucket=s3.bucket, Prefix=f"orgs/{org}/")
    assert not uploads.get("Uploads")


async def test_list_pagination(client: httpx.AsyncClient) -> None:
    org = uuid.uuid4()
    ids = [(await start(client, org, 100, f"v{i}.mkv"))["video_id"] for i in range(3)]
    r = await client.get("/api/v1/videos?limit=2", headers=who(org))
    page1 = r.json()
    assert [v["id"] for v in page1["items"]] == ids[::-1][:2] and page1["next_cursor"]
    r = await client.get(f"/api/v1/videos?limit=2&cursor={page1['next_cursor']}", headers=who(org))
    assert [v["id"] for v in r.json()["items"]] == [ids[0]] and r.json()["next_cursor"] is None
    assert_problem(await client.get("/api/v1/videos?cursor=garbage", headers=who(org)), 422, "VALIDATION_FAILED")


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ({"filename": "notes.txt", "size": 10, "mime": "text/plain"}, "Unsupported file type"),
        ({"filename": "noext", "size": 10, "mime": "video/mp4"}, "Unsupported file type"),
        ({"filename": "a.mp4", "size": 10, "mime": "text/html"}, "Unsupported content type"),
        ({"filename": "a.mp4", "size": 201 * MIB, "mime": "video/mp4"}, "limit"),
    ],
)
async def test_upload_validation(client: httpx.AsyncClient, body: dict[str, object], fragment: str) -> None:
    r = await client.post("/api/v1/videos/uploads", json=body, headers=who(uuid.uuid4()))
    assert fragment in str(assert_problem(r, 422, "VALIDATION_FAILED")["detail"])


@pytest.mark.parametrize(
    ("filename", "mime", "expected"),
    [
        ("Match.MOV", "video/quicktime", ("mov", "video/quicktime")),
        ("clip.mkv", "", ("mkv", "video/x-matroska")),
        ("C:\\Users\\x\\game.webm", "application/octet-stream", ("webm", "video/webm")),
        ("../../etc/game.mp4", "video/mp4", ("mp4", "video/mp4")),
    ],
)
def test_container_of(filename: str, mime: str, expected: tuple[str, str]) -> None:
    assert container_of(filename, mime) == expected
