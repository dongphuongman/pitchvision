"""SCR-51 notifications (M1-BE-19): own rows only, unread count, mark read, quota 80 % / 100 % notifications."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from pitchvision_api.db.models import Notification

from .conftest import Db
from .test_app import assert_problem
from .test_jobs_api import _exec
from .test_quota_api import _video, client, tasks  # noqa: F401 — fixtures

URL = "/api/v1/notifications"


def h(org: uuid.UUID, user: uuid.UUID, plan: str = "free", key: bool = False) -> dict[str, str]:
    out = {"X-Dev-Org": str(org), "X-Dev-User": str(user), "X-Dev-Role": "editor", "X-Dev-Plan": plan}
    return out | ({"Idempotency-Key": uuid.uuid4().hex} if key else {})


async def _add(db: Db, org: uuid.UUID, user: uuid.UUID, type_: str, video: uuid.UUID | None = None,
               age_s: int = 0) -> uuid.UUID:  # fmt: skip
    async def add(s: Any) -> uuid.UUID:
        n = Notification(org_id=org, user_id=user, type=type_, video_id=video, data={"filename": "a.mp4"},
                         created_at=datetime.now(UTC) - timedelta(seconds=age_s))  # fmt: skip
        s.add(n)
        await s.flush()
        return n.id

    nid: uuid.UUID = await _exec(db, org, add)
    return nid


async def test_list_and_mark_read(client: httpx.AsyncClient, migrated: Db) -> None:  # noqa: F811
    org, me, other = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    vid, _ = await _video(migrated, org, 10)
    old = await _add(migrated, org, me, "export_ready", age_s=60)
    new = await _add(migrated, org, me, "job_ready", vid)
    await _add(migrated, org, other, "job_failed", vid)            # someone else's
    await _add(migrated, uuid.uuid4(), me, "job_ready")            # my row in another org
    r = (await client.get(URL, params={"limit": 1}, headers=h(org, me))).json()
    assert r["unread_count"] == 2 and r["next_cursor"]
    first = r["items"][0]
    assert (first["id"], first["type"], first["video_title"], first["path"]) == (
        str(new), "job_ready", "m", f"/videos/{vid}")
    rest = (await client.get(URL, params={"cursor": r["next_cursor"]}, headers=h(org, me))).json()
    assert [n["id"] for n in rest["items"]] == [str(old)] and rest["items"][0]["path"] == "/exports"
    assert_problem(await client.get(URL, params={"cursor": "junk"}, headers=h(org, me)), 422, "VALIDATION_FAILED")

    assert (await client.post(f"{URL}/read", json={"ids": [str(new)]}, headers=h(org, me))).status_code == 204
    r = (await client.get(URL, headers=h(org, me))).json()
    assert r["unread_count"] == 1 and r["items"][0]["read_at"] is not None
    await client.post(f"{URL}/read", json={"all": True}, headers=h(org, me))
    assert (await client.get(URL, headers=h(org, me))).json()["unread_count"] == 0
    assert (await client.get(URL, headers=h(org, other))).json()["unread_count"] == 1  # untouched


async def test_quota_thresholds_notify_once(client: httpx.AsyncClient, migrated: Db) -> None:  # noqa: F811
    org, me = uuid.uuid4(), uuid.uuid4()
    await _video(migrated, org, 90, job="done")
    vids = [(await _video(migrated, org, m))[0] for m in (5, 10, 15)]  # 95 (< 96 = 80 %) → 105 → 120 (100 %)
    for v in vids:
        r = await client.post("/api/v1/jobs", json={"video_id": str(v)}, headers=h(org, me, key=True))
        assert r.status_code == 202, r.text
    items = (await client.get(URL, headers=h(org, me))).json()["items"]
    assert [n["type"] for n in items] == ["quota_100", "quota_80"]
    assert items[0]["data"]["limit"] == 120 and items[0]["path"] == "/settings/billing"
    assert items[1]["data"]["used"] == 105


async def test_no_quota_notification_below_80_percent(client: httpx.AsyncClient, migrated: Db) -> None:  # noqa: F811
    org, me = uuid.uuid4(), uuid.uuid4()
    vids = [(await _video(migrated, org, 100))[0] for _ in range(2)]
    pro = [await client.post("/api/v1/jobs", json={"video_id": str(v)}, headers=h(org, me, "pro", key=True))
           for v in vids]  # fmt: skip
    assert all(r.status_code == 202 for r in pro)  # Pro 900: 100, 200 — no mark
    assert (await client.get(URL, headers=h(org, me))).json()["items"] == []
