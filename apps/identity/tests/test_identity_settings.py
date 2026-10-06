"""SCR-40 Profile / Preferences (PATCH /me, password, sign out others) and SCR-41 upgrade requests (waitlist)."""

from identity_testkit import PASSWORD, Kit, assert_problem, auth
from pitchvision_identity.ratelimit import MemoryRateLimiter
from test_identity_internal import TOKEN
from test_identity_orgs import _member

from identity_testkit import Outbox, build_app  # isort: skip

import httpx


async def test_patch_me_name_and_email_prefs(kit: Kit) -> None:
    c, s = await kit.verified("sam@club.com", "Sam")
    me = (await c.get("/api/v1/me", headers=auth(s))).json()
    assert me["email_prefs"] == {"job_ready": True, "job_failed": True, "export_ready": True}
    r = await c.patch("/api/v1/me", json={"name": "  Sam Coach ", "email_prefs": {"export_ready": False}},
                      headers=auth(s))  # fmt: skip
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Sam Coach"
    assert r.json()["email_prefs"] == {"job_ready": True, "job_failed": True, "export_ready": False}
    r = await c.patch("/api/v1/me", json={"email_prefs": {"job_ready": False}}, headers=auth(s))
    assert r.json()["email_prefs"] == {"job_ready": False, "job_failed": True, "export_ready": False}
    assert_problem(await c.patch("/api/v1/me", json={"email_prefs": {"weekly": True}}, headers=auth(s)), 422,
                   "VALIDATION_FAILED")  # fmt: skip
    assert_problem(await c.patch("/api/v1/me", json={"name": "   "}, headers=auth(s)), 422, "VALIDATION_FAILED")


async def test_change_password_and_sign_out_others(kit: Kit) -> None:
    c, s = await kit.verified("pw@club.com")
    other = kit.browser()
    r = await other.post("/api/v1/auth/login", json={"email": "pw@club.com", "password": PASSWORD})
    assert r.status_code == 200
    body = {"current_password": "wrong password!", "new_password": "a brand new password"}
    assert_problem(await c.post("/api/v1/auth/password/change", json=body, headers=auth(s)), 401, "INVALID_CREDENTIALS")
    body["current_password"] = PASSWORD
    assert (await c.post("/api/v1/auth/password/change", json=body, headers=auth(s))).status_code == 204
    assert (await c.post("/api/v1/auth/refresh")).status_code == 200        # this browser stays signed in
    assert (await other.post("/api/v1/auth/refresh")).status_code == 401    # the other one is signed out
    new = {"email": "pw@club.com", "password": "a brand new password"}
    r = await kit.browser().post("/api/v1/auth/login", json=new)
    assert r.status_code == 200

    third = kit.browser()
    await third.post("/api/v1/auth/login", json={"email": "pw@club.com", "password": "a brand new password"})
    s2 = (await c.post("/api/v1/auth/refresh")).json()
    assert (await c.post("/api/v1/auth/sessions/revoke-others", headers=auth(s2))).status_code == 204
    assert (await third.post("/api/v1/auth/refresh")).status_code == 401
    assert (await c.post("/api/v1/auth/refresh")).status_code == 200


async def test_upgrade_requests(kit: Kit) -> None:
    owner, os_ = await kit.verified("boss@club.com", "Boss")
    org = await kit.org(owner, os_)
    r = await owner.post(f"/api/v1/orgs/{org['id']}/upgrade-requests", json={"plan": "pro", "period": "annual"},
                         headers=auth(os_))  # fmt: skip
    assert r.status_code == 202 and r.json()["owners_notified"] == 0
    ed, es = await _member(kit, owner, os_, org["id"], "ed@club.com", "editor")
    before = len(kit.outbox.to("boss@club.com"))
    r = await ed.post(f"/api/v1/orgs/{org['id']}/upgrade-requests", json={"reason": "quota"}, headers=auth(es))
    assert r.status_code == 202 and r.json()["owners_notified"] == 1 and r.json()["plan"] == "pro"
    mail = kit.outbox.to("boss@club.com")[before:]
    assert len(mail) == 1 and mail[0].subject == "ed asked to upgrade Riverside FC"
    assert "ran out of this month's analysis minutes" in mail[0].text and mail[0].link.endswith("/settings/billing")
    stranger, ss = await kit.verified("x@else.com")
    assert_problem(await stranger.post(f"/api/v1/orgs/{org['id']}/upgrade-requests", json={}, headers=auth(ss)),
                   404, "NOT_FOUND")  # fmt: skip
    assert kit.db.execute("SELECT plan, period, reason, owners_notified FROM upgrade_requests ORDER BY created_at") \
        == [("pro", "annual", None, 0), ("pro", "monthly", "quota", 1)]


async def test_notification_email_respects_prefs(kit: Kit, jwt_key_pem: str) -> None:
    c, s = await kit.verified("pref@club.com")
    org = await kit.org(c, s)
    await c.patch("/api/v1/me", json={"email_prefs": {"job_ready": False}}, headers=auth(s))
    app = build_app(kit.db, jwt_key_pem, Outbox(), MemoryRateLimiter(), internal_token=TOKEN)
    body = {"user_id": s["user"]["id"], "org_id": org["id"], "type": "needs_teams", "path": "/videos/v"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://id.test") as h:
        ok = {"Authorization": f"Bearer {TOKEN}"}
        r = await h.post("/internal/v1/notifications/email", json=body, headers=ok)
        assert r.json() == {"sent": False, "reason": "opted_out"}  # needs_teams follows job_ready
        r = await h.post("/internal/v1/notifications/email", json={**body, "type": "job_failed"}, headers=ok)
        assert r.json()["sent"] is True
    await app.state.engine.dispose()
