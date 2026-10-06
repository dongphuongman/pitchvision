"""M1-BE-06: orgs (region claim, switch), roles + last owner, invites (create, preview, accept, register)."""

from typing import Any

import httpx
import psycopg
import pytest
from identity_testkit import WEB, Kit, assert_problem, auth


async def _member(kit: Kit, owner: httpx.AsyncClient, owner_s: dict[str, Any], org_id: str, email: str, role: str):  # type: ignore[no-untyped-def]
    """Invite `email` with `role` and accept it from a fresh verified browser; returns (browser, session)."""
    r = await owner.post(
        f"/api/v1/orgs/{org_id}/invites", json={"emails": [email], "role": role}, headers=auth(owner_s)
    )
    assert r.status_code == 201, r.text
    token = kit.outbox.last_token(email)
    c, s = await kit.verified(email, name=email.split("@")[0])
    r = await c.post(f"/api/v1/invites/{token}/accept", headers=auth(s))
    assert r.status_code == 200, r.text
    return c, r.json()


async def test_create_org_needs_verified_email(kit: Kit) -> None:
    c = kit.browser()
    s = (await kit.register(c, "new@club.com")).json()
    r = await c.post("/api/v1/orgs", json={"name": "X", "kind": "club", "region": "eu"}, headers=auth(s))
    assert_problem(r, 403, "EMAIL_NOT_VERIFIED")


async def test_create_org_and_switch_carries_org_role_region(kit: Kit) -> None:
    c, s = await kit.verified("owner@club.com")
    org = await kit.org(c, s, name="Riverside FC", kind="academy", region="us", my_role_title="Coach")
    assert org["region"] == "us" and org["kind"] == "academy" and org["name"] == "Riverside FC"
    assert kit.db.execute("SELECT title FROM memberships") == [("Coach",)]

    session = await kit.switch(c, org["id"])
    assert session["active_org_id"] == org["id"]
    assert session["memberships"] == [
        {"org_id": org["id"], "org_name": "Riverside FC", "role": "owner", "region": "us"}
    ]
    claims = await kit.claims(c, session)
    assert (claims["org"], claims["role"], claims["region"]) == (org["id"], "owner", "us")
    assert (claims["plan"], claims["quota_min"]) == ("free", 120)  # no subscription row = Free

    # a paid plan (and a per-org override) reach the token on the next refresh; a lapsed one falls back to Free
    kit.db.execute("INSERT INTO subscriptions (org_id, plan, minutes_per_month) VALUES (%s, 'team', 3000)",
                   (org["id"],))  # fmt: skip
    claims = await kit.claims(c, (await c.post("/api/v1/auth/refresh")).json())
    assert (claims["plan"], claims["quota_min"]) == ("team", 3000)
    kit.db.execute("UPDATE subscriptions SET status = 'past_due'")
    claims = await kit.claims(c, (await c.post("/api/v1/auth/refresh")).json())
    assert (claims["plan"], claims["quota_min"]) == ("free", 120)
    kit.db.execute("DELETE FROM subscriptions")

    # a plain refresh (and a fresh login) stays in / lands in the last active org
    again = (await c.post("/api/v1/auth/refresh")).json()
    assert again["active_org_id"] == org["id"]

    second = await kit.org(c, session, name="Second", region="eu")
    s2 = await kit.switch(c, second["id"])
    assert (await kit.claims(c, s2))["region"] == "eu" and len(s2["memberships"]) == 2
    s1 = await kit.switch(c, org["id"])
    assert (await kit.claims(c, s1))["org"] == org["id"]

    r = await c.get("/api/v1/me", headers=auth(s1))
    assert {m["org_name"] for m in r.json()["memberships"]} == {"Riverside FC", "Second"}


async def test_switch_to_foreign_org_is_forbidden(kit: Kit) -> None:
    a, sa = await kit.verified("a@club.com")
    org = await kit.org(a, sa)
    b, _ = await kit.verified("b@club.com")
    assert_problem(await b.post("/api/v1/auth/refresh", json={"org_id": org["id"]}), 403, "FORBIDDEN")
    assert (await b.post("/api/v1/auth/refresh")).status_code == 200  # the cookie was not consumed by the 403


async def test_org_read_patch_and_region_immutable(kit: Kit) -> None:
    c, s = await kit.verified("o@club.com")
    org = await kit.org(c, s)
    assert (await c.get(f"/api/v1/orgs/{org['id']}", headers=auth(s))).json() == org
    r = await c.patch(f"/api/v1/orgs/{org['id']}", json={"name": "Renamed FC"}, headers=auth(s))
    assert r.status_code == 200 and r.json()["name"] == "Renamed FC" and r.json()["region"] == "eu"
    r = await c.patch(f"/api/v1/orgs/{org['id']}", json={"region": "us"}, headers=auth(s))
    assert_problem(r, 422, "VALIDATION_FAILED")
    with pytest.raises(psycopg.errors.CheckViolation, match="immutable"):
        kit.db.execute("UPDATE orgs SET region = 'us'")

    stranger, ss = await kit.verified("stranger@club.com")
    assert_problem(await stranger.get(f"/api/v1/orgs/{org['id']}", headers=auth(ss)), 404, "NOT_FOUND")
    editor, es = await _member(kit, c, s, org["id"], "ed@club.com", "editor")
    assert (await editor.get(f"/api/v1/orgs/{org['id']}", headers=auth(es))).status_code == 200
    r = await editor.patch(f"/api/v1/orgs/{org['id']}", json={"name": "Hijack"}, headers=auth(es))
    assert_problem(r, 403, "FORBIDDEN")
    assert_problem(await editor.get(f"/api/v1/orgs/{org['id']}/members", headers=auth(es)), 403, "FORBIDDEN")


async def test_roles_and_last_owner(kit: Kit) -> None:
    owner, os_ = await kit.verified("own@club.com")
    org = await kit.org(owner, os_)
    oid = org["id"]
    owner_id = os_["user"]["id"]
    admin, as_ = await _member(kit, owner, os_, oid, "adm@club.com", "admin")
    viewer, vs = await _member(kit, owner, os_, oid, "view@club.com", "viewer")
    admin_id, viewer_id = as_["user"]["id"], vs["user"]["id"]

    members = (await admin.get(f"/api/v1/orgs/{oid}/members", headers=auth(as_))).json()
    assert {(m["email"], m["role"]) for m in members} == {
        ("own@club.com", "owner"),
        ("adm@club.com", "admin"),
        ("view@club.com", "viewer"),
    }
    assert all(m["joined_at"] and m["name"] for m in members)

    url = f"/api/v1/orgs/{oid}/members"
    r = await admin.patch(f"{url}/{viewer_id}", json={"role": "editor"}, headers=auth(as_))
    assert r.status_code == 200 and r.json()["role"] == "editor"
    assert_problem(await viewer.patch(f"{url}/{viewer_id}", json={"role": "admin"}, headers=auth(vs)), 403, "FORBIDDEN")
    # admins can neither mint owners nor touch an owner
    assert_problem(await admin.patch(f"{url}/{admin_id}", json={"role": "owner"}, headers=auth(as_)), 403, "FORBIDDEN")
    assert_problem(await admin.patch(f"{url}/{owner_id}", json={"role": "viewer"}, headers=auth(as_)), 403, "FORBIDDEN")
    assert_problem(await admin.delete(f"{url}/{owner_id}", headers=auth(as_)), 403, "FORBIDDEN")
    # the last owner can neither be demoted nor removed
    assert_problem(await owner.patch(f"{url}/{owner_id}", json={"role": "admin"}, headers=auth(os_)), 409, "LAST_OWNER")
    assert_problem(await owner.delete(f"{url}/{owner_id}", headers=auth(os_)), 409, "LAST_OWNER")
    # with a second owner, the first may step down
    r = await owner.patch(f"{url}/{admin_id}", json={"role": "owner"}, headers=auth(os_))
    assert r.json()["role"] == "owner"
    r = await owner.patch(f"{url}/{owner_id}", json={"role": "admin"}, headers=auth(os_))
    assert r.status_code == 200 and r.json()["role"] == "admin"
    # role change shows up in the next token
    assert (await kit.claims(owner, await kit.switch(owner, oid)))["role"] == "admin"
    # remove + leave
    assert (await admin.delete(f"{url}/{viewer_id}", headers=auth(as_))).status_code == 204
    assert_problem(await admin.delete(f"{url}/{viewer_id}", headers=auth(as_)), 404, "NOT_FOUND")
    assert (await owner.delete(f"{url}/{owner_id}", headers=auth(os_))).status_code == 204  # leaves (was admin)
    s = (await owner.post("/api/v1/auth/refresh")).json()
    assert s["active_org_id"] is None and s["memberships"] == []
    assert_problem(
        await admin.patch(f"{url}/{viewer_id}", json={"role": "x"}, headers=auth(as_)), 422, "VALIDATION_FAILED"
    )


async def test_invites_create_list_preview_accept(kit: Kit) -> None:
    owner, os_ = await kit.verified("boss@club.com", name="Sam")
    org = await kit.org(owner, os_, region="eu")
    oid = org["id"]
    r = await owner.post(
        f"/api/v1/orgs/{oid}/invites",
        json={
            "emails": ["Ana@club.com", "ana@club.com ", "bo@club.com", "boss@club.com"],
            "role": "editor",
            "message": "Welcome!",
        },
        headers=auth(os_),
    )
    assert r.status_code == 201, r.text
    invites = r.json()
    assert [i["email"] for i in invites] == ["ana@club.com", "bo@club.com"]  # deduped, existing member skipped
    assert all(i["role"] == "editor" and i["invited_by"] == os_["user"]["id"] for i in invites)
    mail = kit.outbox.to("ana@club.com")[-1]
    assert mail.link.startswith(f"{WEB}/invite/") and "Welcome!" in mail.text and "Sam" in mail.subject
    listed = (await owner.get(f"/api/v1/orgs/{oid}/invites", headers=auth(os_))).json()
    assert {i["id"] for i in listed} == {i["id"] for i in invites}

    token = kit.outbox.last_token("ana@club.com")
    anon = kit.browser()
    r = await anon.get(f"/api/v1/invites/{token}")
    assert r.json() == {
        "org_name": "Riverside FC",
        "inviter_name": "Sam",
        "email": "ana@club.com",
        "role": "editor",
        "region": "eu",
        "expired": False,
    }
    assert_problem(await anon.get("/api/v1/invites/" + "z" * 40), 404, "NOT_FOUND")
    assert_problem(await anon.post(f"/api/v1/invites/{token}/accept"), 401, "UNAUTHENTICATED")

    # wrong account
    wrong, ws = await kit.verified("wrong@club.com")
    assert_problem(await wrong.post(f"/api/v1/invites/{token}/accept", headers=auth(ws)), 403, "INVITE_EMAIL_MISMATCH")

    # right account: joins, session lands in the org
    ana, as_ = await kit.verified("ana@club.com")
    r = await ana.post(f"/api/v1/invites/{token}/accept", headers=auth(as_))
    assert r.status_code == 200, r.text
    session = r.json()
    assert session["active_org_id"] == oid and "pv_refresh=" in r.headers["set-cookie"]
    assert (await kit.claims(ana, session))["role"] == "editor"
    assert (await anon.get(f"/api/v1/invites/{token}")).json()["expired"] is True
    assert_problem(await ana.post(f"/api/v1/invites/{token}/accept", headers=auth(as_)), 410, "TOKEN_EXPIRED")
    listed = (await owner.get(f"/api/v1/orgs/{oid}/invites", headers=auth(os_))).json()
    assert [i["email"] for i in listed] == ["bo@club.com"]


async def test_invite_expiry_resend_revoke(kit: Kit) -> None:
    owner, os_ = await kit.verified("boss@club.com")
    oid = (await kit.org(owner, os_))["id"]
    [inv] = (
        await owner.post(
            f"/api/v1/orgs/{oid}/invites", json={"emails": ["late@club.com"], "role": "viewer"}, headers=auth(os_)
        )
    ).json()
    rows = kit.db.execute("SELECT expires_at - created_at FROM invites")
    assert rows[0][0].days == 7
    old = kit.outbox.last_token("late@club.com")
    kit.db.execute("UPDATE invites SET expires_at = now() - interval '1 second'")
    late, ls = await kit.verified("late@club.com")
    assert (await late.get(f"/api/v1/invites/{old}")).json()["expired"] is True
    assert_problem(await late.post(f"/api/v1/invites/{old}/accept", headers=auth(ls)), 410, "TOKEN_EXPIRED")

    r = await owner.post(f"/api/v1/orgs/{oid}/invites/{inv['id']}/resend", headers=auth(os_))
    assert r.status_code == 202
    new = kit.outbox.last_token("late@club.com")
    assert new != old
    assert_problem(await late.get(f"/api/v1/invites/{old}"), 404, "NOT_FOUND")  # old link is dead
    assert (await late.get(f"/api/v1/invites/{new}")).json()["expired"] is False

    assert (await owner.delete(f"/api/v1/orgs/{oid}/invites/{inv['id']}", headers=auth(os_))).status_code == 204
    assert_problem(await late.get(f"/api/v1/invites/{new}"), 404, "NOT_FOUND")
    assert_problem(await late.post(f"/api/v1/invites/{new}/accept", headers=auth(ls)), 404, "NOT_FOUND")
    assert (await owner.get(f"/api/v1/orgs/{oid}/invites", headers=auth(os_))).json() == []
    assert_problem(await owner.delete(f"/api/v1/orgs/{oid}/invites/{inv['id']}", headers=auth(os_)), 404, "NOT_FOUND")


async def test_invite_permissions(kit: Kit) -> None:
    owner, os_ = await kit.verified("boss@club.com")
    oid = (await kit.org(owner, os_))["id"]
    ed, es = await _member(kit, owner, os_, oid, "ed@club.com", "editor")
    body = {"emails": ["x@club.com"], "role": "viewer"}
    assert_problem(await ed.post(f"/api/v1/orgs/{oid}/invites", json=body, headers=auth(es)), 403, "FORBIDDEN")
    bad = {"emails": ["x@club.com"], "role": "owner"}
    assert_problem(
        await owner.post(f"/api/v1/orgs/{oid}/invites", json=bad, headers=auth(os_)), 422, "VALIDATION_FAILED"
    )
    empty = {"emails": [], "role": "viewer"}
    assert_problem(
        await owner.post(f"/api/v1/orgs/{oid}/invites", json=empty, headers=auth(os_)), 422, "VALIDATION_FAILED"
    )


async def test_register_with_invite(kit: Kit) -> None:
    owner, os_ = await kit.verified("boss@club.com")
    oid = (await kit.org(owner, os_, region="us"))["id"]
    await owner.post(
        f"/api/v1/orgs/{oid}/invites",
        json={"emails": ["newbie@club.com", "other@club.com"], "role": "admin"},
        headers=auth(os_),
    )
    token = kit.outbox.last_token("newbie@club.com")
    c = kit.browser()
    r = await kit.register(c, "other@club.com", invite_token=token)
    assert_problem(r, 403, "INVITE_EMAIL_MISMATCH")
    sent_before = len(kit.outbox.sent)
    r = await kit.register(c, "NewBie@club.com", invite_token=token, plan="pro")
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["user"]["email_verified"] is True and s["active_org_id"] == oid
    assert s["memberships"] == [{"org_id": oid, "org_name": "Riverside FC", "role": "admin", "region": "us"}]
    claims = await kit.claims(c, s)
    assert (claims["email_verified"], claims["role"], claims["region"]) == (True, "admin", "us")
    assert len(kit.outbox.sent) == sent_before  # no verification email
    assert kit.db.execute("SELECT signup_plan FROM users WHERE email = 'newbie@club.com'") == [("pro",)]
    # invite is used up
    assert_problem(await kit.register(kit.browser(), "z@club.com", invite_token=token), 410, "TOKEN_EXPIRED")


async def test_accepting_invite_verifies_email(kit: Kit) -> None:
    """An unverified user who accepts an invite sent to their address has proven it."""
    owner, os_ = await kit.verified("boss@club.com")
    oid = (await kit.org(owner, os_))["id"]
    await owner.post(
        f"/api/v1/orgs/{oid}/invites", json={"emails": ["u@club.com"], "role": "viewer"}, headers=auth(os_)
    )
    token = kit.outbox.last_token("u@club.com")
    c = kit.browser()
    s = (await kit.register(c, "u@club.com")).json()
    r = await c.post(f"/api/v1/invites/{token}/accept", headers=auth(s))
    assert r.status_code == 200 and r.json()["user"]["email_verified"] is True
