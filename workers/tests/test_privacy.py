"""SCR-40 Data & privacy (worker): cpu.build_data_export and cpu.privacy_sweep (expired archives, org / account
erasure pulled from identity-svc) against real Postgres (pv_worker, RLS) + S3; identity is faked."""
from __future__ import annotations

import io
import json
import uuid
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from pitchvision_schemas import keys
from pitchvision_worker.config import WorkerConfig
from pitchvision_worker.privacy import PrivacyDeps, process_erasures, run_build_data_export, run_privacy_sweep
from pitchvision_worker.repository import org_tx
from pitchvision_worker.storage import ObjectStore
from sqlalchemy import Engine, create_engine, text
from worker_testkit import S3_BUCKET, s3_test_client

ID_URL = "http://identity.test"


class FakeIdentity:
    def __init__(self) -> None:
        self.erasures: list[dict[str, str]] = []
        self.done: list[tuple[str, str]] = []
        self.fetched: list[str] = []

    def fetch(self, url: str, headers: dict[str, str]) -> httpx.Response:
        assert headers == {"Authorization": "Bearer svc"}
        self.fetched.append(url)
        req = httpx.Request("GET", url)
        if "/internal/v1/erasures" in url:
            return httpx.Response(200, json=self.erasures, request=req)
        return httpx.Response(200, json={"user": {"email": "sam@club.com"}, "memberships": []}, request=req)

    def post(self, url: str, body: dict[str, Any], headers: dict[str, str]) -> httpx.Response:
        self.done.append((url.rsplit("/", 2)[-2], body["region"]))
        return httpx.Response(200, json={"completed": True})


@pytest.fixture
def worker_db(pg_worker_url: str) -> Iterator[Engine]:
    e = create_engine(pg_worker_url)
    yield e
    e.dispose()


@pytest.fixture
def deps(tmp_path: Any, worker_db: Engine, s3_endpoint: str) -> tuple[PrivacyDeps, FakeIdentity]:
    fake = FakeIdentity()
    cfg = WorkerConfig.from_env({"PV_IDENTITY_INTERNAL_URL": ID_URL, "PV_INTERNAL_TOKEN": "svc", "PV_REGION": "eu"})
    store = ObjectStore(s3_test_client(s3_endpoint), S3_BUCKET)
    return PrivacyDeps(cfg, worker_db, store, fetch=fake.fetch, post=fake.post, tmp_root=str(tmp_path)), fake


def _seed(engine: Engine, org: uuid.UUID, user: uuid.UUID, other: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    """Two videos (one by `user`, one by `other`), a team, positions, a notification for `user`."""
    mine, theirs = uuid.uuid4(), uuid.uuid4()
    with org_tx(engine, org) as c:
        for vid, by in ((mine, user), (theirs, other)):
            c.execute(text("INSERT INTO videos (id, org_id, created_by, source_type, status, title) "
                           "VALUES (:v, :o, :u, 'upload', 'ready', :t)"), {"v": vid, "o": org, "u": by, "t": str(by)})
        c.execute(text("INSERT INTO teams (video_id, org_id, cluster, name, color) VALUES (:v, :o, 0, 'Reds', '#f00')"),
                  {"v": theirs, "o": org})  # fmt: skip
        c.execute(text("INSERT INTO positions (video_id, org_id, ts_ms, track_id, team, px, py) VALUES "
                       "(:v, :o, 0, 1, 0, 10.5, 20.25), (:v, :o, 200, 1, 0, 11, 20.5)"), {"v": theirs, "o": org})
        c.execute(text("INSERT INTO notifications (org_id, user_id, type, data, email_status) "
                       "VALUES (:o, :u, 'quota_80', '{}', 'skipped')"), {"o": org, "u": user})  # fmt: skip
    return mine, theirs


def _data_export(engine: Engine, org: uuid.UUID, user: uuid.UUID, scope: str, **cols: Any) -> uuid.UUID:
    eid = uuid.uuid4()
    with org_tx(engine, org) as c:
        c.execute(text("INSERT INTO data_exports (id, org_id, user_id, scope) VALUES (:e, :o, :u, :s)"),
                  {"e": eid, "o": org, "u": user, "s": scope})  # fmt: skip
        if cols:
            sets = ", ".join(f"{k} = :{k}" for k in cols)
            c.execute(text(f"UPDATE data_exports SET {sets} WHERE id = :e"), {"e": eid, **cols})
    return eid


def _row(engine: Engine, org: uuid.UUID, table: str, **where: Any) -> list[dict[str, Any]]:
    cond = " AND ".join(f"{k} = :{k}" for k in where) or "true"
    with org_tx(engine, org) as c:
        return [dict(r) for r in c.execute(text(f"SELECT * FROM {table} WHERE {cond}"), where).mappings()]


def _zip(store: ObjectStore, key: str) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(store.get_bytes(key)))


def test_build_data_export_org_and_account_scope(deps: tuple[PrivacyDeps, FakeIdentity]) -> None:
    d, fake = deps
    org, user, other = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    mine, theirs = _seed(d.engine, org, user, other)

    eid = _data_export(d.engine, org, user, "org")
    assert run_build_data_export(str(org), str(eid), d) == "done"
    row = _row(d.engine, org, "data_exports", id=eid)[0]
    key = keys.data_export_key(str(org), str(eid))
    assert (row["status"], row["object_key"]) == ("done", key)
    assert row["size_bytes"] == len(d.store.get_bytes(key))
    assert timedelta(days=6.9) < row["expires_at"] - row["finished_at"] <= timedelta(days=7)
    z = _zip(d.store, key)
    assert {"README.txt", "account.json", "notifications.json", "org/videos.json", "org/teams.json",
            f"org/positions/{theirs}.csv", f"org/positions/{mine}.csv"} <= set(z.namelist())  # fmt: skip
    assert json.loads(z.read("account.json"))["user"]["email"] == "sam@club.com"
    assert fake.fetched == [f"{ID_URL}/internal/v1/users/{user}/export"]
    assert {v["id"] for v in json.loads(z.read("org/videos.json"))} == {str(mine), str(theirs)}
    assert z.read(f"org/positions/{theirs}.csv").decode().splitlines() == [
        "ts_ms,track_id,team,px,py,vx,vy,q", "0,1,0,10.5,20.25,,,", "200,1,0,11.0,20.5,,,"]
    assert [n["type"] for n in json.loads(z.read("notifications.json"))] == ["quota_80"]
    notes = _row(d.engine, org, "notifications", type="data_export_ready")
    assert [(n["user_id"], n["email_status"], n["data"]["export_id"]) for n in notes] == [(user, "pending", str(eid))]
    assert run_build_data_export(str(org), str(eid), d) == "skipped"  # redelivery

    acc = _data_export(d.engine, org, user, "account")
    assert run_build_data_export(str(org), str(acc), d) == "done"
    z = _zip(d.store, keys.data_export_key(str(org), str(acc)))
    assert [v["id"] for v in json.loads(z.read("org/videos.json"))] == [str(mine)]
    assert "org/teams.json" not in z.namelist() and not any(n.startswith("org/positions/") for n in z.namelist())
    assert run_build_data_export(str(uuid.uuid4()), str(acc), d, row_waits=0) == "wait_row"  # other org: invisible


def test_build_data_export_failure(deps: tuple[PrivacyDeps, FakeIdentity]) -> None:
    d, _ = deps
    org, user = uuid.uuid4(), uuid.uuid4()

    def down(url: str, headers: dict[str, str]) -> httpx.Response:
        raise httpx.ConnectError("identity down")

    d.fetch = down
    eid = _data_export(d.engine, org, user, "account")
    assert run_build_data_export(str(org), str(eid), d) == "failed"
    row = _row(d.engine, org, "data_exports", id=eid)[0]
    assert row["status"] == "failed" and row["error"]["code"] == "INTERNAL"


def test_privacy_sweep_erases_orgs_and_accounts(deps: tuple[PrivacyDeps, FakeIdentity]) -> None:
    d, fake = deps
    gone_org, kept_org, gone_user, kept_user = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    _seed(d.engine, gone_org, kept_user, kept_user)
    mine, theirs = _seed(d.engine, kept_org, gone_user, kept_user)
    obj = [keys.normalized_key(str(gone_org), str(uuid.uuid4())), keys.data_export_key(str(gone_org), "x"),
           keys.normalized_key(str(kept_org), str(theirs))]  # fmt: skip
    for k in obj:
        d.store.put_bytes(k, b"x", "video/mp4")
    archive = keys.data_export_key(str(kept_org), "gone-user")
    d.store.put_bytes(archive, b"zip", "application/zip")
    _data_export(d.engine, kept_org, gone_user, "account", status="done", object_key=archive)
    org_req, user_req = str(uuid.uuid4()), str(uuid.uuid4())
    fake.erasures = [{"id": org_req, "kind": "org", "subject_id": str(gone_org), "requested_at": "x"},
                     {"id": user_req, "kind": "user", "subject_id": str(gone_user), "requested_at": "x"}]

    # first pass: everything goes, nothing acked yet
    assert process_erasures(d) == (2, 0)
    assert _row(d.engine, gone_org, "videos") == [] and _row(d.engine, gone_org, "notifications") == []
    with org_tx(d.engine, gone_org) as c:
        assert c.execute(text("SELECT count(*) FROM positions_org")).scalar() == 0
    assert [d.store.exists(k) for k in obj] == [False, False, True]
    assert not d.store.exists(archive)
    assert _row(d.engine, kept_org, "notifications", user_id=gone_user) == []
    assert _row(d.engine, kept_org, "data_exports", user_id=gone_user) == []
    assert {v["id"]: v["created_by"] for v in _row(d.engine, kept_org, "videos")} == {mine: None, theirs: kept_user}
    assert len(_row(d.engine, kept_org, "teams")) == 1
    # not due yet → nothing; final pass ≥ 4 h later (here: at once) → acked
    assert process_erasures(d) == (0, 0)
    assert process_erasures(d, final_after_s=0) == (2, 2)
    assert sorted(fake.done) == sorted([(org_req, "eu"), (user_req, "eu")])
    # identity still lists one (lost ack) → acked again without another pass
    fake.erasures = fake.erasures[:1]
    assert process_erasures(d, final_after_s=0) == (0, 1)


def test_privacy_sweep_purges_expired_archives(deps: tuple[PrivacyDeps, FakeIdentity]) -> None:
    d, _ = deps
    org, user = uuid.uuid4(), uuid.uuid4()
    old, soon = datetime.now(UTC) - timedelta(hours=1), datetime.now(UTC) + timedelta(days=3)
    keys_ = [keys.data_export_key(str(org), f"a{i}") for i in range(2)]
    for k in keys_:
        d.store.put_bytes(k, b"zip", "application/zip")
    expired = _data_export(d.engine, org, user, "account", status="done", object_key=keys_[0], expires_at=old)
    kept = _data_export(d.engine, org, user, "account", status="done", object_key=keys_[1], expires_at=soon)
    r = run_privacy_sweep(d)
    assert r.archives_purged >= 1
    assert [_row(d.engine, org, "data_exports", id=e)[0]["object_key"] for e in (expired, kept)] == [None, keys_[1]]
    assert [d.store.exists(k) for k in keys_] == [False, True]
