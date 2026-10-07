"""SCR-40 D03 / D04 (DLG-08): delete org / account, and the regional erasure hand-off (/internal/v1/erasures)."""

import httpx
from identity_testkit import Kit, Outbox, assert_problem, auth, build_app
from pitchvision_identity.ratelimit import MemoryRateLimiter
from test_identity_internal import TOKEN
from test_identity_orgs import _member

SVC = {"Authorization": f"Bearer {TOKEN}"}


async def test_delete_org_owner_only_with_typed_name(kit: Kit) -> None:
    owner, s = await kit.verified("owner@club.com")
    org = await kit.org(owner, s, name="Riverside FC")
    admin, admin_s = await _member(kit, owner, s, org["id"], "admin@club.com", "admin")
    url = f"/api/v1/orgs/{org['id']}"
    assert_problem(await admin.request("DELETE", url, json={"confirm_name": "Riverside FC"}, headers=auth(admin_s)),
                   403, "FORBIDDEN")  # fmt: skip
    assert_problem(await owner.request("DELETE", url, json={"confirm_name": "riverside fc"}, headers=auth(s)),
                   422, "VALIDATION_FAILED")  # fmt: skip
    r = await owner.request("DELETE", url, json={"confirm_name": " Riverside FC "}, headers=auth(s))
    assert r.status_code == 204, r.text
    assert_problem(await owner.get(url, headers=auth(s)), 404, "NOT_FOUND")
    assert_problem(await admin.get(url, headers=auth(admin_s)), 404, "NOT_FOUND")
    # sessions keep working, now without an org
    r = await admin.post("/api/v1/auth/refresh")
    assert r.status_code == 200 and r.json()["active_org_id"] is None and r.json()["memberships"] == []
    rows = kit.db.execute("SELECT kind, regions, completed_at FROM erasure_requests WHERE subject_id = %s",
                          (org["id"],))  # fmt: skip
    assert rows == [("org", ["eu"], None)]


async def test_delete_account_rules(kit: Kit) -> None:
    owner, s = await kit.verified("owner@club.com")
    shared = await kit.org(owner, s, name="Shared FC")
    solo = await kit.org(owner, s, name="Solo FC", region="us")
    member, member_s = await _member(kit, owner, s, shared["id"], "member@club.com", "editor")

    preview = (await owner.get("/api/v1/me/deletion-preview", headers=auth(s))).json()
    assert [o["name"] for o in preview["blocking"]] == ["Shared FC"]
    assert [(o["name"], o["region"]) for o in preview["deleted_with_account"]] == [("Solo FC", "us")]
    assert_problem(await owner.request("DELETE", "/api/v1/me", json={"confirm_email": "nope@club.com"},
                                       headers=auth(s)), 422, "VALIDATION_FAILED")  # fmt: skip
    body = assert_problem(await owner.request("DELETE", "/api/v1/me", json={"confirm_email": "owner@club.com"},
                                              headers=auth(s)), 409, "LAST_OWNER")  # fmt: skip
    assert "Shared FC" in body["detail"]

    # hand ownership over → the account can go; Solo FC goes with it, Shared FC stays
    r = await owner.patch(f"/api/v1/orgs/{shared['id']}/members/{member_s['user']['id']}", json={"role": "owner"},
                          headers=auth(s))  # fmt: skip
    assert r.status_code == 200, r.text
    r = await owner.request("DELETE", "/api/v1/me", json={"confirm_email": " Owner@Club.com "}, headers=auth(s))
    assert r.status_code == 204, r.text
    assert "refresh" in r.headers.get("set-cookie", "").lower()
    assert (await owner.post("/api/v1/auth/refresh")).status_code == 401
    assert (await member.get(f"/api/v1/orgs/{shared['id']}", headers=auth(member_s))).status_code == 200
    rows = kit.db.execute("SELECT kind, subject_id::text, regions FROM erasure_requests WHERE subject_id IN (%s, %s)"
                          " ORDER BY kind", (solo["id"], s["user"]["id"]))  # fmt: skip
    assert rows == [("org", solo["id"], ["us"]), ("user", s["user"]["id"], ["eu", "us"])]
    assert kit.db.execute("SELECT count(*) FROM users WHERE email = 'owner@club.com'") == [(0,)]


async def test_delete_account_without_orgs_is_complete_at_once(kit: Kit) -> None:
    c, s = await kit.verified("lone@club.com")
    r = await c.request("DELETE", "/api/v1/me", json={"confirm_email": "lone@club.com"}, headers=auth(s))
    assert r.status_code == 204
    rows = kit.db.execute("SELECT regions, completed_at IS NOT NULL FROM erasure_requests WHERE subject_id = %s",
                          (s["user"]["id"],))  # fmt: skip
    assert rows == [([], True)]


async def test_internal_erasures_and_user_export(kit: Kit, jwt_key_pem: str) -> None:
    owner, s = await kit.verified("owner@club.com", "Sam Owner")
    eu = await kit.org(owner, s, name="EU FC")
    us = await kit.org(owner, s, name="US FC", region="us")
    app = build_app(kit.db, jwt_key_pem, Outbox(), MemoryRateLimiter(), internal_token=TOKEN)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://id.test") as c:
        assert (await c.get("/internal/v1/erasures?region=eu")).status_code == 401
        export = (await c.get(f"/internal/v1/users/{s['user']['id']}/export", headers=SVC)).json()
        assert export["user"]["email"] == "owner@club.com" and export["user"]["has_password"] is True
        assert sorted(m["org_name"] for m in export["memberships"]) == ["EU FC", "US FC"]
        assert len(export["sessions"]) == 1

        r = await owner.request("DELETE", "/api/v1/me", json={"confirm_email": "owner@club.com"}, headers=auth(s))
        assert r.status_code == 204
        assert (await c.get(f"/internal/v1/users/{s['user']['id']}/export", headers=SVC)).status_code == 404
        mine = {eu["id"], us["id"], s["user"]["id"]}
        eu_rows = [e for e in (await c.get("/internal/v1/erasures?region=eu&limit=200", headers=SVC)).json()
                   if e["subject_id"] in mine]
        assert sorted((e["kind"], e["subject_id"]) for e in eu_rows) == sorted(
            [("org", eu["id"]), ("user", s["user"]["id"])])
        user_req = next(e["id"] for e in eu_rows if e["kind"] == "user")
        r = await c.post(f"/internal/v1/erasures/{user_req}/done", json={"region": "eu"}, headers=SVC)
        assert r.json() == {"completed": False}  # us still to go
        left = (await c.get("/internal/v1/erasures?region=eu&limit=200", headers=SVC)).json()
        assert [e["kind"] for e in left if e["subject_id"] in mine] == ["org"]
        us_rows = [e for e in (await c.get("/internal/v1/erasures?region=us&limit=200", headers=SVC)).json()
                   if e["subject_id"] in mine]
        assert sorted((e["kind"], e["subject_id"]) for e in us_rows) == sorted(
            [("org", us["id"]), ("user", s["user"]["id"])])
        r = await c.post(f"/internal/v1/erasures/{user_req}/done", json={"region": "us"}, headers=SVC)
        assert r.json() == {"completed": True}
        org_req = next(e["id"] for e in eu_rows if e["kind"] == "org")
        r = await c.post(f"/internal/v1/erasures/{org_req}/done", json={"region": "us"}, headers=SVC)
        assert r.status_code == 409
    await app.state.engine.dispose()
