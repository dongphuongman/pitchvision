"""/internal/v1/notifications/email (M1-BE-19): service token, membership / verification checks, email copy."""

import uuid

import httpx
from identity_testkit import WEB, Kit, Outbox, build_app
from pitchvision_identity.ratelimit import MemoryRateLimiter

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
