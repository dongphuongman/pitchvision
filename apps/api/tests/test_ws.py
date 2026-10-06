"""M1-BE-12: WebSocket /api/v1/ws — JWT in ?token=, org-scoped job subscriptions, Redis fan-out, heartbeat, limits."""

import json
import time
import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
import redis
from fastapi.testclient import TestClient
from pitchvision_api.auth import JwksClient
from pitchvision_api.main import create_app
from pitchvision_api.settings import Settings
from starlette.testclient import WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from .conftest import Db
from .jwt_helpers import ISSUER, JWKS_URL, FakeIdentity


def make_client(migrated: Db, redis_url: str, identity: FakeIdentity, **overrides: Any) -> TestClient:
    settings = Settings(database_url=migrated.app_url, redis_url=redis_url, jwks_url=JWKS_URL, jwt_issuer=ISSUER,
                        **overrides)  # fmt: skip
    app = create_app(settings)
    app.state.jwks = JwksClient(JWKS_URL, transport=identity.transport())
    return TestClient(app)


@pytest.fixture
def identity() -> FakeIdentity:
    return FakeIdentity()


@pytest.fixture
def client(migrated: Db, redis_url: str, identity: FakeIdentity) -> Iterator[TestClient]:
    with make_client(migrated, redis_url, identity) as c:
        yield c


@pytest.fixture
def pub(redis_url: str) -> Iterator[redis.Redis]:
    r = redis.Redis.from_url(redis_url)
    yield r
    r.close()


def seed_job(db: Db, org: uuid.UUID, **job: Any) -> uuid.UUID:
    """Insert video + job as the schema owner (bypasses RLS: test setup only)."""
    vid, jid = uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(db.conninfo()) as conn:
        conn.execute("INSERT INTO videos (id, org_id, created_by, status) VALUES (%s, %s, %s, 'ready')",
                     (vid, org, uuid.uuid4()))  # fmt: skip
        conn.execute(
            "INSERT INTO jobs (id, video_id, org_id, image_tag, config, status, progress, stage, detail, error) "
            "VALUES (%s, %s, %s, 't', '{}', %s, %s, %s, %s, %s)",
            (jid, vid, org, job.get("status", "inferring"), job.get("progress", 42.5), job.get("stage", "infer"),
             json.dumps(job.get("detail", {"segments": {"done": 1, "running": 2, "total": 4}, "eta_s": 60})),
             json.dumps(job["error"]) if "error" in job else None),
        )  # fmt: skip
    return jid


def connect(client: TestClient, token: str | None) -> WebSocketTestSession:
    return client.websocket_connect("/api/v1/ws" + (f"?token={token}" if token else ""))


def publish_until_delivered(pub: redis.Redis, topic: str, event: dict[str, Any]) -> None:
    deadline = time.monotonic() + 5
    while pub.publish(topic, json.dumps(event)) < 1:  # the hub subscribes asynchronously
        assert time.monotonic() < deadline, "hub never subscribed"
        time.sleep(0.05)


@pytest.mark.parametrize("token", [None, "garbage"])
def test_rejects_missing_or_bad_token(client: TestClient, token: str | None) -> None:
    with connect(client, token) as ws, pytest.raises(WebSocketDisconnect) as exc:
        ws.receive_json()
    assert exc.value.code == 4401


def test_wrong_region_is_forbidden(client: TestClient, identity: FakeIdentity) -> None:
    with connect(client, identity.token(region="us")) as ws, pytest.raises(WebSocketDisconnect) as exc:
        ws.receive_json()
    assert exc.value.code == 4403


def test_subscribe_snapshot_and_events(client: TestClient, identity: FakeIdentity, migrated: Db,
                                       pub: redis.Redis) -> None:  # fmt: skip
    org = uuid.uuid4()
    jid = seed_job(migrated, org)
    topic = f"job:{jid}"
    with connect(client, identity.token(org=org, role="viewer")) as ws:
        ws.send_json({"op": "subscribe", "topic": topic})
        assert ws.receive_json() == {"type": "subscribed", "topic": topic, "data": {"status": "inferring"}}
        assert ws.receive_json() == {"topic": topic, "type": "progress", "data": {
            "stage": "infer", "pct": 42.5, "fps": None, "eta_s": 60, "segments": {"done": 1, "running": 2, "total": 4}}}
        event = {"topic": topic, "type": "progress", "data": {"stage": "infer", "pct": 61.0, "fps": None,
                                                              "eta_s": 30, "segments": None}}  # fmt: skip
        publish_until_delivered(pub, topic, event)
        assert ws.receive_json() == event
        done = {"topic": topic, "type": "completed", "data": {"video_id": "v"}}
        pub.publish(topic, json.dumps(done))
        assert ws.receive_json() == done
        ws.send_json({"op": "unsubscribe", "topic": topic})
        assert ws.receive_json() == {"type": "unsubscribed", "topic": topic}
        pub.publish(topic, json.dumps(event))
        ws.send_json({"op": "ping"})
        assert ws.receive_json() == {"type": "pong"}  # nothing from the topic in between
    hub = client.app.state.events  # type: ignore[attr-defined]
    deadline = time.monotonic() + 3
    while topic in hub.topics() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert topic not in hub.topics()  # last listener gone → Redis UNSUBSCRIBE


def test_cross_org_subscribe_denied(client: TestClient, identity: FakeIdentity, migrated: Db,
                                    pub: redis.Redis) -> None:  # fmt: skip
    jid = seed_job(migrated, uuid.uuid4())
    topic = f"job:{jid}"
    with connect(client, identity.token(org=uuid.uuid4())) as ws:
        ws.send_json({"op": "subscribe", "topic": topic})
        assert ws.receive_json() == {"type": "error", "topic": topic,
                                     "data": {"code": "NOT_FOUND", "detail": "job not found"}}  # fmt: skip
        assert pub.publish(topic, json.dumps({"topic": topic, "type": "completed", "data": {"video_id": "x"}})) == 0
        ws.send_json({"op": "ping"})
        assert ws.receive_json() == {"type": "pong"}


def test_terminal_snapshots(client: TestClient, identity: FakeIdentity, migrated: Db) -> None:
    org = uuid.uuid4()
    failed = seed_job(migrated, org, status="failed", error={"code": "NO_WIDE_SHOTS", "retryable": False})
    with connect(client, identity.token(org=org)) as ws:
        ws.send_json({"op": "subscribe", "topic": f"job:{failed}"})
        ws.receive_json()
        assert ws.receive_json() == {"topic": f"job:{failed}", "type": "failed",
                                     "data": {"code": "NO_WIDE_SHOTS", "retryable": False}}  # fmt: skip


def test_validation_and_rate_limit(migrated: Db, redis_url: str, identity: FakeIdentity) -> None:
    org = uuid.uuid4()
    jid = seed_job(migrated, org)
    with make_client(migrated, redis_url, identity, ws_subscribe_burst=2) as client, \
            connect(client, identity.token(org=org)) as ws:  # fmt: skip
        ws.send_text("not json")
        assert ws.receive_json()["data"]["code"] == "VALIDATION_FAILED"
        ws.send_json({"op": "subscribe", "topic": "video:123"})
        assert ws.receive_json()["data"]["code"] == "VALIDATION_FAILED"
        ws.send_json({"op": "subscribe"})
        assert ws.receive_json()["data"]["code"] == "VALIDATION_FAILED"
        for _ in range(2):
            ws.send_json({"op": "subscribe", "topic": f"job:{jid}"})
            assert ws.receive_json()["type"] == "subscribed"
            ws.receive_json()  # snapshot
        ws.send_json({"op": "subscribe", "topic": f"job:{jid}"})
        assert ws.receive_json() == {"type": "error", "topic": f"job:{jid}", "data": {
            "code": "RATE_LIMITED", "detail": "too many subscribe requests; slow down"}}


def test_heartbeat(migrated: Db, redis_url: str, identity: FakeIdentity) -> None:
    with make_client(migrated, redis_url, identity, ws_heartbeat_s=0.2) as client, \
            connect(client, identity.token()) as ws:  # fmt: skip
        msg = ws.receive_json()
        assert msg["type"] == "ping" and isinstance(msg["ts"], int)


def test_dev_auth_headers_local_only(migrated: Db, redis_url: str, identity: FakeIdentity) -> None:
    org = uuid.uuid4()
    jid = seed_job(migrated, org)
    with make_client(migrated, redis_url, identity, env="local", dev_auth=True) as client, \
            client.websocket_connect("/api/v1/ws", headers={"X-Dev-Org": str(org)}) as ws:  # fmt: skip
        ws.send_json({"op": "subscribe", "topic": f"job:{jid}"})
        assert ws.receive_json()["type"] == "subscribed"
