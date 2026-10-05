"""M1-BE-08 API side: URL allowlist, preview policy, from-url → row + `ingest.download` task (no network)."""

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pitchvision_api.db import make_engine, make_sessionmaker, org_session
from pitchvision_api.db.models import Video
from pitchvision_api.main import create_app
from pitchvision_api.services.ingest import policy_violation
from pitchvision_api.services.tasks import CeleryTaskQueue, TaskTimeout, TaskUnavailable
from pitchvision_api.services.youtube_url import InvalidYoutubeUrl, parse_youtube_url
from pitchvision_api.settings import Settings
from sqlalchemy import select

from .conftest import Db
from .test_app import DEAD_DB, assert_problem

VID = "dQw4w9WgXcQ"
CANON = f"https://www.youtube.com/watch?v={VID}"
GOOD_INFO: dict[str, Any] = {
    "title": "Final",
    "duration_s": 5400.0,
    "width": 1920,
    "height": 1080,
    "fps": 25.0,
    "is_live": False,
    "live_status": "was_live",
    "availability": "public",
    "age_limit": 0,
    "thumbnail_url": "https://i.ytimg.com/vi/x/maxresdefault.jpg",
}


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.youtube.com/watch?v={VID}",
        f"http://youtube.com/watch?v={VID}&t=42s&list=PL123",
        f"https://m.youtube.com/watch?feature=share&v={VID}",
        f"https://youtu.be/{VID}?si=abc",
        f"youtu.be/{VID}",
        f"https://www.youtube.com/shorts/{VID}",
        f"https://www.youtube.com/live/{VID}?feature=share",
        f"https://WWW.YouTube.com/embed/{VID}",
        f"https://www.youtube.com:443/watch?v={VID}",
        f"  https://www.youtube.com/watch?v={VID}#t=1  ",
    ],
)
def test_accepted_urls_are_canonicalised(url: str) -> None:
    assert parse_youtube_url(url).url == CANON


@pytest.mark.parametrize(
    "url",
    [
        "",
        "https://vimeo.com/123",
        f"https://youtube.com.evil.com/watch?v={VID}",
        f"https://evil.com/watch?v={VID}#youtube.com",
        f"https://www.youtube.com@evil.com/watch?v={VID}",
        f"https://user:pw@www.youtube.com/watch?v={VID}",
        f"https://www.youtube.com:8080/watch?v={VID}",
        f"ftp://www.youtube.com/watch?v={VID}",
        f"file:///etc/passwd?v={VID}",
        "http://169.254.169.254/latest/meta-data",
        f"https://music.youtube.com/watch?v={VID}",
        f"https://www.youtube.com/watch?v={VID}&v=aaaaaaaaaaa",
        "https://www.youtube.com/watch?v=short",
        "https://www.youtube.com/playlist?list=PL123",
        "https://www.youtube.com/@channel",
        f"https://youtu.be/{VID}/extra",
        f"https://www.youtube.com/watch?v={VID}\nHost: evil",
        f"https://www.youtube.com\\@evil.com/watch?v={VID}",
        f"https://www.youtube.com/watch?v={VID}" + "&x=1" * 600,
    ],
)
def test_rejected_urls(url: str) -> None:
    with pytest.raises(InvalidYoutubeUrl):
        parse_youtube_url(url)


@pytest.mark.parametrize(
    ("patch", "reason"),
    [
        ({}, None),
        ({"duration_s": 3 * 3600 + 1}, "too_long"),
        ({"duration_s": 3 * 3600}, None),
        ({"duration_s": None}, "unknown_duration"),
        ({"is_live": True}, "live"),
        ({"live_status": "is_upcoming"}, "live"),
        ({"availability": "private"}, "private"),
        ({"availability": "subscriber_only"}, "members_only"),
        ({"availability": "needs_auth"}, "needs_auth"),
        ({"availability": "unlisted"}, None),
        ({"age_limit": 18}, "age_restricted"),
    ],
)
def test_policy(patch: dict[str, Any], reason: str | None) -> None:
    assert policy_violation(GOOD_INFO | patch, 3 * 3600) == reason


@dataclass
class FakeTasks:
    probe: Any = field(default_factory=lambda: {"ok": True, "info": GOOD_INFO})
    sent: list[tuple[str, dict[str, Any], str]] = field(default_factory=list)
    calls: list[tuple[str, dict[str, Any], str, float]] = field(default_factory=list)
    down: bool = False

    async def send(self, name: str, kwargs: dict[str, Any], queue: str) -> str:
        if self.down:
            raise TaskUnavailable("redis down")
        self.sent.append((name, kwargs, queue))
        return "task-id"

    async def call(self, name: str, kwargs: dict[str, Any], queue: str, timeout_s: float) -> Any:
        self.calls.append((name, kwargs, queue, timeout_s))
        if isinstance(self.probe, Exception):
            raise self.probe
        return self.probe


def who(org: uuid.UUID, role: str = "editor") -> dict[str, str]:
    return {"X-Dev-Org": str(org), "X-Dev-Role": role}


async def _client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await app.state.engine.dispose()


@pytest.fixture
def tasks() -> FakeTasks:
    return FakeTasks()


@pytest.fixture
async def offline(tasks: FakeTasks) -> AsyncIterator[httpx.AsyncClient]:
    """No database: preview never touches it."""
    app = create_app(Settings(env="local", dev_auth=True, database_url=DEAD_DB))
    app.state.tasks = tasks
    async for c in _client(app):
        yield c


@pytest.fixture
async def client(migrated: Db, tasks: FakeTasks) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(Settings(env="local", dev_auth=True, database_url=migrated.app_url))
    app.state.tasks = tasks
    async for c in _client(app):
        yield c


async def test_preview_ok(offline: httpx.AsyncClient, tasks: FakeTasks) -> None:
    r = await offline.post(
        "/api/v1/videos/from-url/preview", json={"url": f"youtu.be/{VID}"}, headers=who(uuid.uuid4())
    )
    assert r.status_code == 200, r.text
    assert r.json() == {
        "url": CANON, "video_id": VID, "title": "Final", "duration_s": 5400.0, "width": 1920, "height": 1080,
        "fps": 25.0, "is_live": False, "availability": "public", "thumbnail_url": GOOD_INFO["thumbnail_url"],
    }  # fmt: skip
    assert tasks.calls == [("ingest.probe_url", {"url": CANON}, "ingest", 20.0)]


@pytest.mark.parametrize(
    ("probe", "status", "code", "reason"),
    [
        ({"ok": True, "info": GOOD_INFO | {"duration_s": 4 * 3600}}, 422, "VALIDATION_FAILED", "too_long"),
        ({"ok": True, "info": GOOD_INFO | {"is_live": True}}, 422, "VALIDATION_FAILED", "live"),
        ({"ok": False, "reason": "private", "detail": "Private video"}, 422, "VALIDATION_FAILED", "private"),
        ({"ok": False, "reason": "age_restricted"}, 422, "VALIDATION_FAILED", "age_restricted"),
        ({"ok": False, "reason": "blocked", "detail": "bot check"}, 502, "INGEST_FAILED", "blocked"),
        (TaskTimeout("x"), 504, "INGEST_FAILED", "timeout"),
        (TaskUnavailable("x"), 503, "INGEST_FAILED", "queue_unavailable"),
        ("garbage", 502, "INGEST_FAILED", "probe_failed"),
    ],
)
async def test_preview_rejections(
    offline: httpx.AsyncClient, tasks: FakeTasks, probe: Any, status: int, code: str, reason: str
) -> None:
    tasks.probe = probe
    r = await offline.post("/api/v1/videos/from-url/preview", json={"url": CANON}, headers=who(uuid.uuid4()))
    body = assert_problem(r, status, code)
    assert body["reason"] == reason and body["detail"]


async def test_preview_bad_url_never_reaches_worker(offline: httpx.AsyncClient, tasks: FakeTasks) -> None:
    r = await offline.post(
        "/api/v1/videos/from-url/preview", json={"url": "https://vimeo.com/1"}, headers=who(uuid.uuid4())
    )
    assert assert_problem(r, 422, "VALIDATION_FAILED")["reason"] == "invalid_url"
    assert tasks.calls == []


async def test_from_url_creates_ingesting_video(client: httpx.AsyncClient, tasks: FakeTasks, migrated: Db) -> None:
    org = uuid.uuid4()
    body = {"url": f"https://youtu.be/{VID}", "rights_confirmed": True, "title": "Cup final"}
    r = await client.post("/api/v1/videos/from-url", json=body, headers=who(org))
    assert r.status_code == 202, r.text
    v = r.json()
    assert v["status"] == "ingesting" and v["source_type"] == "youtube" and v["source_url"] == CANON
    assert v["title"] == "Cup final" and v["rights_confirmed_at"]
    assert tasks.sent == [("ingest.download", {"video_id": v["id"], "org_id": str(org), "url": CANON}, "ingest")]
    got = await client.get(f"/api/v1/videos/{v['id']}", headers=who(org, "viewer"))
    assert got.json()["status"] == "ingesting"


async def test_from_url_requires_rights_and_editor(client: httpx.AsyncClient, tasks: FakeTasks) -> None:
    org = uuid.uuid4()
    r = await client.post("/api/v1/videos/from-url", json={"url": CANON, "rights_confirmed": False}, headers=who(org))
    assert_problem(r, 422, "VALIDATION_FAILED")
    r = await client.post("/api/v1/videos/from-url", json={"url": CANON}, headers=who(org))
    assert_problem(r, 422, "VALIDATION_FAILED")
    r = await client.post("/api/v1/videos/from-url", json={"url": CANON, "rights_confirmed": True},
                          headers=who(org, "viewer"))  # fmt: skip
    assert_problem(r, 403, "FORBIDDEN")
    assert tasks.sent == []


async def test_from_url_rolls_back_when_queue_down(client: httpx.AsyncClient, tasks: FakeTasks, migrated: Db) -> None:
    org = uuid.uuid4()
    tasks.down = True
    r = await client.post("/api/v1/videos/from-url", json={"url": CANON, "rights_confirmed": True}, headers=who(org))
    assert assert_problem(r, 503, "INGEST_FAILED")["reason"] == "queue_unavailable"
    engine = make_engine(migrated.app_url)
    try:
        async with org_session(make_sessionmaker(engine), org) as s:
            assert (await s.scalars(select(Video))).all() == []
    finally:
        await engine.dispose()


async def test_celery_queue_fails_fast_without_broker() -> None:
    q = CeleryTaskQueue("redis://127.0.0.1:1/0")
    with pytest.raises(TaskUnavailable):
        await q.send("ingest.download", {}, "ingest")
