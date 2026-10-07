"""SCR-40 Data & privacy (regional): summary counts for DLG-08, GDPR data export request / download."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from pitchvision_api.db.models import DataExport
from pitchvision_api.services.tasks import TASK_BUILD_DATA_EXPORT

from .conftest import Db
from .test_app import assert_problem
from .test_exports_api import client, tasks  # noqa: F401  (fixtures)
from .test_ingest_api import FakeTasks, who
from .test_jobs_api import _exec, _video

URL = "/api/v1/privacy"


def as_user(org: uuid.UUID, user: uuid.UUID, role: str = "viewer") -> dict[str, str]:
    return who(org, role) | {"X-Dev-User": str(user)}


async def _set(db: Db, org: uuid.UUID, export_id: str, **values: Any) -> None:
    async def upd(s: Any) -> None:
        d = await s.get(DataExport, uuid.UUID(export_id))
        for k, v in values.items():
            setattr(d, k, v)

    await _exec(db, org, upd)


async def test_summary_counts_live_videos(client: httpx.AsyncClient, migrated: Db) -> None:  # noqa: F811
    org = uuid.uuid4()
    await _video(migrated, org)
    await _video(migrated, org)
    await _video(migrated, org, status="deleted")
    await _video(migrated, uuid.uuid4())  # another org
    r = await client.get(URL, headers=who(org, "owner"))
    assert r.status_code == 200, r.text
    assert r.json() == {"videos": 2, "exports": 0, "data_export": None}


async def test_data_export_lifecycle(client: httpx.AsyncClient, migrated: Db, tasks: FakeTasks) -> None:  # noqa: F811
    org, admin, viewer = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    r = await client.post(f"{URL}/data-exports", headers=as_user(org, admin, "admin"))
    assert r.status_code == 202, r.text
    d = r.json()
    assert d["scope"] == "org" and d["status"] == "queued"
    assert tasks.sent[-1] == (TASK_BUILD_DATA_EXPORT, {"export_id": d["id"], "org_id": str(org)}, "cpu")
    assert_problem(await client.post(f"{URL}/data-exports", headers=as_user(org, admin, "admin")), 409, "CONFLICT")
    # a viewer gets their own data only, and cannot see the admin's export
    r = await client.post(f"{URL}/data-exports", headers=as_user(org, viewer))
    assert r.status_code == 202 and r.json()["scope"] == "account"
    assert (await client.get(URL, headers=as_user(org, viewer))).json()["data_export"]["scope"] == "account"
    dl = f"{URL}/data-exports/{d['id']}/download"
    assert_problem(await client.get(dl, headers=as_user(org, viewer)), 404, "NOT_FOUND")
    assert_problem(await client.get(dl, headers=as_user(org, admin, "admin")), 409, "CONFLICT")

    now = datetime.now(UTC)
    key = f"exports/orgs/{org}/data/{d['id']}.zip"
    await _set(migrated, org, d["id"], status="done", object_key=key, size_bytes=1234, finished_at=now,
               expires_at=now + timedelta(days=7))  # fmt: skip
    r = await client.get(dl, headers=as_user(org, admin, "admin"))
    assert r.status_code == 200, r.text
    assert r.json()["filename"].startswith("pitchvision-data-") and key in r.json()["url"]
    assert r.headers["cache-control"] == "private, no-store"
    s = (await client.get(URL, headers=as_user(org, admin, "admin"))).json()
    assert s["data_export"]["status"] == "done" and s["data_export"]["size_bytes"] == 1234
    # a new one may start once the last is done
    assert (await client.post(f"{URL}/data-exports", headers=as_user(org, admin, "admin"))).status_code == 202

    await _set(migrated, org, d["id"], expires_at=now - timedelta(minutes=1))
    assert_problem(await client.get(dl, headers=as_user(org, admin, "admin")), 410, "NOT_FOUND")


async def test_data_export_queue_down(client: httpx.AsyncClient, tasks: FakeTasks) -> None:  # noqa: F811
    tasks.down = True
    r = await client.post(f"{URL}/data-exports", headers=who(uuid.uuid4(), "owner"))
    assert_problem(r, 503, "INTERNAL")
