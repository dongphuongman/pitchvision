"""cpu.delete_video_objects (AD §14 erasure) against SeaweedFS (S3) + Postgres (RLS, pv_app)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from pitchvision_schemas import keys
from pitchvision_worker import erasure
from pitchvision_worker.config import WorkerConfig
from pitchvision_worker.repository import org_tx
from sqlalchemy import Engine, create_engine, text
from worker_testkit import S3_BUCKET, S3_KEY, S3_SECRET, s3_test_client


@pytest.fixture
def s3(s3_endpoint: str) -> Any:
    return s3_test_client(s3_endpoint)


@pytest.fixture
def db(pg_app_url: str) -> Iterator[Engine]:
    e = create_engine(pg_app_url)
    yield e
    e.dispose()


def video_keys(org: str, vid: str) -> list[str]:
    """At least one object in every storage class, several in some (pagination with page_size=2)."""
    job = "j1"
    return [
        keys.source_key(org, vid, "mp4"),
        keys.normalized_key(org, vid), keys.manifest_key(org, vid), keys.shots_key(org, vid),
        *(keys.segment_prefix(org, vid, job, i) + f for i in range(3) for f in ("detections.parquet", "_SUCCESS")),
        keys.job_results_prefix(org, vid, job) + "models/team_model.json",
        keys.job_results_prefix(org, vid, job) + "stages/shots/shots.json",
        *(keys.tile_key(org, vid, job, 0, n) for n in range(5)),
        keys.export_key(org, vid, "e1", "mp4"),
    ]  # fmt: skip


def put_all(s3: Any, names: list[str]) -> None:
    for k in names:
        s3.put_object(Bucket=S3_BUCKET, Key=k, Body=b"x")


def listed(s3: Any, prefix: str) -> list[str]:
    out, token = [], None
    while True:
        kw: dict[str, Any] = {"Bucket": S3_BUCKET, "Prefix": prefix}
        if token:
            kw["ContinuationToken"] = token
        page = s3.list_objects_v2(**kw)
        out += [o["Key"] for o in page.get("Contents", [])]
        if not page.get("IsTruncated"):
            return out
        token = page["NextContinuationToken"]


def test_erase_every_prefix_across_pages_and_keep_neighbours(s3: Any) -> None:
    org, vid, other = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    mine, theirs = video_keys(org, vid), video_keys(org, other)
    put_all(s3, mine + theirs)
    upload = s3.create_multipart_upload(Bucket=S3_BUCKET, Key=keys.source_key(org, vid, "mov"))["UploadId"]

    got = erasure.erase_prefixes(s3, S3_BUCKET, keys.video_prefixes(org, vid), page_size=2)
    assert got.objects == len(mine) and got.uploads == 1
    assert all(listed(s3, p) == [] for p in keys.video_prefixes(org, vid))
    assert sorted(k for p in keys.video_prefixes(org, other) for k in listed(s3, p)) == sorted(theirs)
    with pytest.raises(Exception, match="NoSuchUpload"):
        s3.list_parts(Bucket=S3_BUCKET, Key=keys.source_key(org, vid, "mov"), UploadId=upload)
    # idempotent: a second pass finds nothing
    assert erasure.erase_prefixes(s3, S3_BUCKET, keys.video_prefixes(org, vid), page_size=2) == erasure.Erased(0, 0)


def _video(db: Engine, org: uuid.UUID, vid: uuid.UUID, status: str) -> None:
    with org_tx(db, org) as c:
        c.execute(text("INSERT INTO videos (id, org_id, created_by, source_type, status) "
                       "VALUES (:v, :o, :u, 'upload', :s)"),
                  {"v": vid, "o": org, "u": uuid.uuid4(), "s": status})  # fmt: skip


def _deps(db: Engine, s3: Any, s3_endpoint: str) -> erasure.ErasureDeps:
    env = {"PV_STORAGE_BUCKET": S3_BUCKET, "PV_STORAGE_ENDPOINT": s3_endpoint, "PV_STORAGE_ACCESS_KEY": S3_KEY,
           "PV_STORAGE_SECRET_KEY": S3_SECRET}  # fmt: skip
    return erasure.ErasureDeps(WorkerConfig.from_env(env), db, s3, page_size=3)


def test_task_waits_for_the_deleted_row(db: Engine, s3: Any, s3_endpoint: str) -> None:
    org, vid = uuid.uuid4(), uuid.uuid4()
    names = video_keys(str(org), str(vid))
    put_all(s3, names)
    _video(db, org, vid, "ready")
    deps = _deps(db, s3, s3_endpoint)

    # the API published before committing: the row isn't `deleted` yet → wait, delete nothing
    assert erasure.run_delete_video_objects(str(org), str(vid), deps).kind == "wait_row"
    # the request rolled back: give up after the waits, objects stay
    gave_up = erasure.run_delete_video_objects(str(org), str(vid), deps, row_waits=erasure.ROW_WAIT_TRIES)
    assert gave_up.kind == "skipped"
    assert len(listed(s3, keys.video_prefixes(str(org), str(vid))[-1])) == 1  # exports/… still there

    with org_tx(db, org) as c:
        c.execute(text("UPDATE videos SET status = 'deleted', deleted_at = now() WHERE id = :v"), {"v": vid})
    out = erasure.run_delete_video_objects(str(org), str(vid), deps)
    assert out.kind == "erased" and out.erased.objects == len(names)
    assert all(listed(s3, p) == [] for p in keys.video_prefixes(str(org), str(vid)))

    # a row that doesn't exist (another org can't see it either) is swept without waiting
    assert erasure.run_delete_video_objects(str(uuid.uuid4()), str(vid), deps).kind == "erased"

    scheduled: list[tuple[dict[str, Any], float]] = []
    erasure.after_erasure(str(org), str(vid), lambda kw, delay: scheduled.append((kw, delay)))
    assert scheduled == [({"org_id": str(org), "video_id": str(vid), "final": True}, erasure.FINAL_PASS_AFTER_S)]


def test_task_is_registered_on_the_cpu_queue() -> None:
    from pitchvision_worker.celery_app import create_app

    app = create_app("memory://")
    app.loader.import_default_modules()
    assert erasure.TASK_DELETE_VIDEO_OBJECTS in app.tasks
    assert app.amqp.router.route({}, erasure.TASK_DELETE_VIDEO_OBJECTS)["queue"].name == "cpu"
