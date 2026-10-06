"""M1-BE-15/16: manifest (signed tile template), signed tile serving, teams, tracks — real Postgres (RLS) + S3."""

import gzip
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
import psycopg
import pytest
from pitchvision_api.db.models import Job, Team, Video
from pitchvision_api.main import create_app
from pitchvision_api.services.tile_signing import sign, tile_scope
from pitchvision_api.settings import Settings
from pitchvision_schemas import (
    Coverage,
    Manifest,
    PipelineConfig,
    PitchCalibration,
    Shot,
    ShotType,
    TeamInfo,
    Tile,
    TileFrame,
    keys,
)

from .conftest import S3, Db, s3_client
from .test_app import assert_problem
from .test_ingest_api import who
from .test_jobs_api import _exec

KEY = "test-tile-signing-key-0123456789abcdef"


@pytest.fixture
async def client(migrated: Db, s3: S3) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(env="local", dev_auth=True, database_url=migrated.app_url, storage_endpoint=s3.endpoint,
                        storage_bucket=s3.bucket, storage_access_key=s3.access_key, storage_secret_key=s3.secret_key,
                        storage_region="us-east-1", tile_signing_key=KEY)  # fmt: skip
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await app.state.engine.dispose()


async def _video_job(db: Db, org: uuid.UUID, status: str = "done", detail: dict[str, Any] | None = None
                     ) -> tuple[uuid.UUID, uuid.UUID]:
    async def add(s: Any) -> tuple[uuid.UUID, uuid.UUID]:
        v = Video(org_id=org, created_by=uuid.uuid4(), status="ready", source_type="upload", title="match")
        s.add(v)
        await s.flush()
        j = Job(video_id=v.id, org_id=org, image_tag="t", config=PipelineConfig().model_dump(mode="json"),
                status=status, detail=detail, finished_at=datetime.now(UTC) if status == "done" else None)
        s.add(j)
        await s.flush()
        return v.id, j.id

    ids: tuple[uuid.UUID, uuid.UUID] = await _exec(db, org, add)
    return ids


def _manifest(org: uuid.UUID, vid: uuid.UUID, job: uuid.UUID, revision: int = 0) -> Manifest:
    return Manifest(
        video_id=str(vid), job_id=str(job), revision=revision, fps=25, width=1280, height=720, duration_ms=25_000,
        frame_count=625, tile_count=3,
        tile_url_template=keys.tile_prefix(str(org), str(vid), str(job), revision) + "{tile_no}.json.gz",
        shots=[Shot(t0_ms=0, t1_ms=25_000, type=ShotType.main_wide, conf=0.9)],
        coverage=Coverage(main_wide_pct=100, homography_ok_pct=0),
        teams=[TeamInfo(cluster=0, name="Team A", color="#e53935"), TeamInfo(cluster=1, name="Team B",
                                                                            color="#1e88e5")],
        pitch=PitchCalibration(status="unavailable"),
    )  # fmt: skip


def _put(s3: S3, key: str, body: bytes) -> None:
    s3_client(s3.endpoint).put_object(Bucket=s3.bucket, Key=key, Body=body)


def _publish(s3: S3, org: uuid.UUID, vid: uuid.UUID, job: uuid.UUID, tiles: int = 3) -> Manifest:
    m = _manifest(org, vid, job)
    _put(s3, keys.manifest_key(str(org), str(vid)), m.model_dump_json().encode())
    for n in range(tiles):
        tile = Tile(tile_no=n, t0_ms=n * 10_000, t1_ms=min((n + 1) * 10_000, 25_000), fps=25,
                    shots=[(n * 250, n * 250 + 1, ShotType.main_wide)],
                    frames=[TileFrame(f=n * 250, hq=None, objs=[(1, 0, 0, 10, 20, 30, 80, 0.9, None, None)])])
        _put(s3, keys.tile_key(str(org), str(vid), str(job), 0, n), gzip.compress(tile.model_dump_json().encode()))
    return m


async def test_manifest_signed_template_and_tile(client: httpx.AsyncClient, migrated: Db, s3: S3) -> None:
    org = uuid.uuid4()
    vid, job = await _video_job(migrated, org)
    stored = _publish(s3, org, vid, job)
    r = await client.get(f"/api/v1/results/{vid}/manifest", headers=who(org, "viewer"))
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "private, no-store"
    m = Manifest.model_validate(r.json())
    assert m.model_dump(exclude={"tile_url_template"}) == stored.model_dump(exclude={"tile_url_template"})
    prefix = f"/api/v1/tiles/{vid}/{job}/r0/{{tile_no}}.json.gz?exp="
    assert m.tile_url_template.startswith(prefix) and "&sig=" in m.tile_url_template
    exp = int(m.tile_url_template.split("exp=")[1].split("&")[0])
    assert 6 * 3600 - 60 < exp - time.time() <= 6 * 3600

    url = m.tile_url_template.replace("{tile_no}", "000001")
    t = await client.get(url)  # no bearer: the signature is the credential
    assert t.status_code == 200, t.text
    assert t.headers["content-encoding"] == "gzip"
    assert t.headers["cache-control"] == "public, max-age=31536000, immutable" and t.headers.get("etag")
    tile = Tile.model_validate(t.json())  # httpx decoded the gzip transfer
    assert tile.tile_no == 1 and tile.frames[0].objs[0][0] == 1

    # a valid signature for a tile that doesn't exist
    missing = await client.get(m.tile_url_template.replace("{tile_no}", "000007"))
    assert_problem(missing, 404, "NOT_FOUND")
    assert missing.headers["cache-control"] == "no-store"


async def test_tile_signature_rejections(client: httpx.AsyncClient, migrated: Db, s3: S3) -> None:
    org = uuid.uuid4()
    vid, job = await _video_job(migrated, org)
    _publish(s3, org, vid, job)
    exp = int(time.time()) + 600
    sig = sign(KEY.encode(), tile_scope(vid, job, 0), exp)
    base = f"/api/v1/tiles/{vid}/{job}"
    ok = await client.get(f"{base}/r0/000000.json.gz", params={"exp": exp, "sig": sig})
    assert ok.status_code == 200
    bad = [
        (f"{base}/r0/000000.json.gz", {"exp": exp, "sig": sig[:-2] + ("AA" if not sig.endswith("AA") else "BB")}),
        (f"{base}/r0/000000.json.gz", {"exp": exp + 1, "sig": sig}),  # tampered expiry
        (f"{base}/r1/000000.json.gz", {"exp": exp, "sig": sig}),  # other revision
        (f"/api/v1/tiles/{vid}/{uuid.uuid4()}/r0/000000.json.gz", {"exp": exp, "sig": sig}),  # other job
        (f"/api/v1/tiles/{uuid.uuid4()}/{job}/r0/000000.json.gz", {"exp": exp, "sig": sig}),  # other video
    ]
    for path, params in bad:
        r = await client.get(path, params=params)
        assert_problem(r, 403, "FORBIDDEN")
        assert r.headers["cache-control"] == "no-store"
    past = int(time.time()) - 1
    expired = await client.get(f"{base}/r0/000000.json.gz",
                               params={"exp": past, "sig": sign(KEY.encode(), tile_scope(vid, job, 0), past)})
    assert_problem(expired, 403, "FORBIDDEN")
    other_key = sign(b"another-key", tile_scope(vid, job, 0), exp)
    assert_problem(await client.get(f"{base}/r0/000000.json.gz", params={"exp": exp, "sig": other_key}), 403,
                   "FORBIDDEN")  # fmt: skip
    assert (await client.get(f"{base}/r0/000000.json.gz", params={"exp": exp})).status_code == 422


async def test_manifest_states_and_cross_org(client: httpx.AsyncClient, migrated: Db, s3: S3) -> None:
    org, other = uuid.uuid4(), uuid.uuid4()
    vid, job = await _video_job(migrated, org)
    # done job but its manifest isn't there (e.g. a W3 stub run)
    r = await client.get(f"/api/v1/results/{vid}/manifest", headers=who(org))
    assert assert_problem(r, 404, "NOT_FOUND")["reason"] == "manifest_missing"
    _publish(s3, org, vid, job)
    assert (await client.get(f"/api/v1/results/{vid}/manifest", headers=who(org))).status_code == 200
    # another org: the video does not exist for it
    assert_problem(await client.get(f"/api/v1/results/{vid}/manifest", headers=who(other)), 404, "NOT_FOUND")
    assert_problem(await client.get(f"/api/v1/results/{vid}/teams", headers=who(other)), 404, "NOT_FOUND")
    assert_problem(await client.get(f"/api/v1/results/{vid}/tracks", headers=who(other)), 404, "NOT_FOUND")
    # first analysis still running → 409 processing; nothing at all → 404
    running_vid, running_job = await _video_job(migrated, org, status="inferring")
    r = await client.get(f"/api/v1/results/{running_vid}/manifest", headers=who(org))
    body = assert_problem(r, 409, "CONFLICT")
    assert body["reason"] == "processing" and body["job_id"] == str(running_job)
    vid2, job2 = await _video_job(migrated, org, status="failed")
    assert_problem(await client.get(f"/api/v1/results/{vid2}/manifest", headers=who(org)), 404, "NOT_FOUND")
    # the stored manifest belongs to a newer job that is about to be marked done
    vid3, job3 = await _video_job(migrated, org)
    _put(s3, keys.manifest_key(str(org), str(vid3)), _manifest(org, vid3, uuid.uuid4()).model_dump_json().encode())
    assert assert_problem(await client.get(f"/api/v1/results/{vid3}/manifest", headers=who(org)), 409,
                          "CONFLICT")["reason"] == "processing"  # fmt: skip
    assert job2 and job3


async def test_teams_from_db_override_manifest(client: httpx.AsyncClient, migrated: Db, s3: S3) -> None:
    org = uuid.uuid4()
    vid, job = await _video_job(migrated, org)
    _publish(s3, org, vid, job)

    async def add(s: Any) -> None:
        s.add(Team(video_id=vid, org_id=org, cluster=0, name="Reds", color="#ff0000", confirmed=True))
        s.add(Team(video_id=vid, org_id=org, cluster=1, name="Team B", color="#1e88e5"))

    await _exec(migrated, org, add)
    teams = (await client.get(f"/api/v1/results/{vid}/teams", headers=who(org, "viewer"))).json()["items"]
    assert [(t["cluster"], t["name"], t["color"], t["confirmed"]) for t in teams] == [
        (0, "Reds", "#ff0000", True), (1, "Team B", "#1e88e5", False)]  # fmt: skip
    m = (await client.get(f"/api/v1/results/{vid}/manifest", headers=who(org, "viewer"))).json()
    assert m["teams"] == [{"cluster": 0, "name": "Reds", "color": "#ff0000", "confirmed": True, "attack_h1": None},
                          {"cluster": 1, "name": "Team B", "color": "#1e88e5", "confirmed": False,
                           "attack_h1": None}]  # fmt: skip


async def test_tracks_keyset_paging_through_positions_org(client: httpx.AsyncClient, migrated: Db) -> None:
    org, other = uuid.uuid4(), uuid.uuid4()
    vid, _ = await _video_job(migrated, org)
    with psycopg.connect(migrated.conninfo()) as conn:  # the worker writes positions; the owner stands in here
        for ts in range(0, 2000, 200):
            conn.execute("INSERT INTO positions (video_id, org_id, ts_ms, track_id, team, px, py, vx, vy, q) VALUES "
                         "(%s, %s, %s, 1, 0, 10.5, 20, 2.5, 0, 0.9), (%s, %s, %s, -1, NULL, NULL, NULL, NULL, NULL, "
                         "NULL)", (vid, org, ts, vid, org, ts))  # fmt: skip
        conn.execute("INSERT INTO positions (video_id, org_id, ts_ms, track_id) VALUES (%s, %s, 0, 9)", (vid, other))
    seen: list[tuple[int, int]] = []
    cursor: str | None = None
    pages = 0
    while True:
        params: dict[str, Any] = {"from_ms": 0, "to_ms": 1000, "limit": 3} | ({"cursor": cursor} if cursor else {})
        r = await client.get(f"/api/v1/results/{vid}/tracks", params=params, headers=who(org, "viewer"))
        assert r.status_code == 200, r.text
        page = r.json()
        seen += [(p["ts_ms"], p["track_id"]) for p in page["items"]]
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == [(ts, t) for ts in range(0, 1000, 200) for t in (-1, 1)] and pages == 4
    first = (await client.get(f"/api/v1/results/{vid}/tracks", headers=who(org))).json()["items"]
    assert len(first) == 20 and first[1] == {"ts_ms": 0, "track_id": 1, "team": 0, "px": 10.5, "py": 20.0,
                                             "vx": 2.5, "vy": 0.0, "q": pytest.approx(0.9)}  # fmt: skip
    assert first[0]["px"] is None and first[0]["team"] is None
    too_wide = await client.get(f"/api/v1/results/{vid}/tracks", params={"from_ms": 0, "to_ms": 700_000},
                                headers=who(org))  # fmt: skip
    assert_problem(too_wide, 422, "VALIDATION_FAILED")
    assert_problem(await client.get(f"/api/v1/results/{vid}/tracks", params={"cursor": "%%%"}, headers=who(org)),
                   422, "VALIDATION_FAILED")  # fmt: skip


async def test_latest_job_pitch_calibration_unavailable(client: httpx.AsyncClient, migrated: Db) -> None:
    org = uuid.uuid4()
    vid, _ = await _video_job(migrated, org, detail={"pitch_calibration": "unavailable"})
    v = (await client.get(f"/api/v1/videos/{vid}", headers=who(org))).json()
    assert v["latest_job"]["pitch_calibration"] == "unavailable"
