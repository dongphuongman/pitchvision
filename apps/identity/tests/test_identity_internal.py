"""/internal/v1/notifications/email (M1-BE-19): service token, membership / verification checks, email copy."""

import uuid

import httpx
from identity_testkit import WEB, Kit, Outbox, build_app
from pitchvision_identity.ratelimit import MemoryRateLimiter
from test_identity_orgs import _member

TOKEN = "svc-secret-token"


async def test_notification_email(kit: Kit, jwt_key_pem: str) -> None:
    owner, s = await kit.verified("coach@club.com", "Sam Coach")
    org = await kit.org(owner, s)
    user_id = s["user"]["id"]
    outbox = Outbox()
    app = build_app(kit.db, jwt_key_pem, outbox, MemoryRateLimiter(), internal_token=TOKEN)
    body = {"user_id": user_id, "org_id": org["id"], "type": "job_ready", "path": "/videos/abc",
            "video_title": "France vs Switzerland", "data": {"job_id": "j"}}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://id.test") as c:
        url = "/internal/v1/notifications/email"
        assert (await c.post(url, json=body)).status_code == 401
        assert (await c.post(url, json=body, headers={"Authorization": "Bearer nope"})).status_code == 401
        ok = {"Authorization": f"Bearer {TOKEN}"}
        r = await c.post(url, json=body, headers=ok)
        assert r.json() == {"sent": True, "reason": None}
        msg = outbox.to("coach@club.com")[-1]
        assert msg.subject == "France vs Switzerland is ready" and msg.link == f"{WEB}/videos/abc"
        assert "Hi Sam Coach," in msg.text

        other_org = {**body, "org_id": str(uuid.uuid4())}
        assert (await c.post(url, json=other_org, headers=ok)).json() == {"sent": False, "reason": "not_member"}
        quiet = {**body, "type": "quota_80"}
        assert (await c.post(url, json=quiet, headers=ok)).json()["reason"] == "no_email_for_type"
        bad = {**body, "path": "https://evil.example/x"}
        assert (await c.post(url, json=bad, headers=ok)).status_code == 422
        assert len(outbox.sent) == 1
    await app.state.engine.dispose()
    # no token configured (the default kit app): the route does not exist
    r = await kit.browser().post("/internal/v1/notifications/email", json=body, headers={"Authorization": "Bearer x"})
    assert r.status_code == 404


async def test_member_events(kit: Kit, jwt_key_pem: str) -> None:

    owner, s = await kit.verified("boss@events.com", "Boss")
    org = await kit.org(owner, s, name="Events FC")
    _, admin_s = await _member(kit, owner, s, org["id"], "admin@events.com", "admin")
    _, viewer_s = await _member(kit, owner, s, org["id"], "viewer@events.com", "viewer")
    app = build_app(kit.db, jwt_key_pem, Outbox(), MemoryRateLimiter(), internal_token=TOKEN)
    ok = {"Authorization": f"Bearer {TOKEN}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://id.test") as c:
        assert (await c.get("/internal/v1/member-events?region=eu")).status_code == 401
        rows = [e for e in (await c.get("/internal/v1/member-events?region=eu&limit=200", headers=ok)).json()
                if e["org_id"] == org["id"]]
        # admin joined → owner told; viewer joined → owner + admin told
        assert [(e["user_name"], e["role"], sorted(e["recipients"])) for e in rows] == [
            ("admin", "admin", [s["user"]["id"]]),
            ("viewer", "viewer", sorted([s["user"]["id"], admin_s["user"]["id"]])),
        ]
        assert rows[0]["org_name"] == "Events FC" and rows[1]["user_id"] == viewer_s["user"]["id"]
        assert (await c.post(f"/internal/v1/member-events/{rows[0]['id']}/done", headers=ok)).status_code == 204
        left = [e["id"] for e in (await c.get("/internal/v1/member-events?region=eu&limit=200", headers=ok)).json()]
        assert rows[0]["id"] not in left and rows[1]["id"] in left
        us = (await c.get("/internal/v1/member-events?region=us&limit=200", headers=ok)).json()
        assert all(e["org_id"] != org["id"] for e in us)
    await app.state.engine.dispose()
