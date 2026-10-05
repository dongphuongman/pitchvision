"""M1-BE-05: register / verify / login / refresh rotation + reuse detection / logout / forgot+reset / JWKS + claims /
unverified delete / rate limits."""

import pytest
from identity_testkit import PASSWORD, WEB, Kit, assert_problem, auth
from pitchvision_identity.security import KeyRing
from pitchvision_identity.settings import Settings
from pydantic import ValidationError


async def test_register_sets_cookie_and_sends_verification(kit: Kit) -> None:
    c = kit.browser()
    r = await kit.register(c, "  Sam@Club.COM ")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["token_type"] == "bearer" and body["expires_in"] == 900
    assert body["user"]["email"] == "sam@club.com" and body["user"]["email_verified"] is False
    assert body["memberships"] == [] and body["active_org_id"] is None
    cookie = r.headers["set-cookie"].lower()
    for attr in ("pv_refresh=", "httponly", "secure", "samesite=lax", "path=/api/v1/auth", "max-age=2592000"):
        assert attr in cookie, cookie
    assert r.headers["cache-control"] == "no-store"
    [mail] = kit.outbox.to("sam@club.com")
    assert mail.link.startswith(f"{WEB}/verify-email?token=") and mail.link in mail.text and mail.link in mail.html
    # only the hash is stored
    token = kit.outbox.last_token("sam@club.com")
    assert kit.db.execute("SELECT count(*) FROM email_tokens WHERE token_hash = %s", (token,)) == [(0,)]


async def test_register_validation_and_duplicates(kit: Kit) -> None:
    c = kit.browser()
    assert_problem(await kit.register(c, "a@b.co", password="short"), 422, "VALIDATION_FAILED")
    assert_problem(await kit.register(c, "not-an-email"), 422, "VALIDATION_FAILED")
    assert (await kit.register(c, "dup@club.com")).status_code == 201
    assert_problem(await kit.register(c, " DUP@club.com"), 409, "EMAIL_TAKEN")


async def test_verify_email_single_use_and_expiry(kit: Kit) -> None:
    c = kit.browser()
    await kit.register(c, "v@club.com")
    token = kit.outbox.last_token("v@club.com")
    r = await c.post("/api/v1/auth/verify-email", json={"token": token})
    assert r.status_code == 200 and r.json()["user"]["email_verified"] is True
    assert (await kit.claims(c, r.json()))["email_verified"] is True
    assert_problem(await c.post("/api/v1/auth/verify-email", json={"token": token}), 410, "TOKEN_EXPIRED")
    assert_problem(await c.post("/api/v1/auth/verify-email", json={"token": "x" * 43}), 410, "TOKEN_EXPIRED")

    await kit.register(c, "late@club.com")
    kit.db.execute("UPDATE email_tokens SET expires_at = now() - interval '1 second' WHERE used_at IS NULL")
    late = kit.outbox.last_token("late@club.com")
    assert_problem(await c.post("/api/v1/auth/verify-email", json={"token": late}), 410, "TOKEN_EXPIRED")


async def test_resend_verification_invalidates_old_link_and_is_rate_limited(kit: Kit) -> None:
    c = kit.browser()
    session = (await kit.register(c, "r@club.com")).json()
    first = kit.outbox.last_token("r@club.com")
    assert_problem(await c.post("/api/v1/auth/verify-email/resend"), 401, "UNAUTHENTICATED")
    for _ in range(3):
        assert (await c.post("/api/v1/auth/verify-email/resend", headers=auth(session))).status_code == 202
    r = await c.post("/api/v1/auth/verify-email/resend", headers=auth(session))
    assert_problem(r, 429, "RATE_LIMITED")
    assert int(r.headers["retry-after"]) > 0
    assert len(kit.outbox.to("r@club.com")) == 4
    assert_problem(await c.post("/api/v1/auth/verify-email", json={"token": first}), 410, "TOKEN_EXPIRED")
    r = await c.post("/api/v1/auth/verify-email", json={"token": kit.outbox.last_token("r@club.com")})
    assert r.status_code == 200


async def test_login_success_and_uniform_failure(kit: Kit) -> None:
    await kit.verified("l@club.com")
    c = kit.browser()
    r = await c.post("/api/v1/auth/login", json={"email": "L@Club.com", "password": PASSWORD})
    assert r.status_code == 200 and r.json()["user"]["email"] == "l@club.com"
    assert "pv_refresh=" in r.headers["set-cookie"]
    wrong = await c.post("/api/v1/auth/login", json={"email": "l@club.com", "password": "nope-nope-nope"})
    unknown = await c.post("/api/v1/auth/login", json={"email": "ghost@club.com", "password": PASSWORD})
    b1, b2 = assert_problem(wrong, 401, "INVALID_CREDENTIALS"), assert_problem(unknown, 401, "INVALID_CREDENTIALS")
    assert {k: v for k, v in b1.items() if k != "instance"} == {k: v for k, v in b2.items() if k != "instance"}


async def test_login_rate_limit_locks_after_five_failures(kit: Kit) -> None:
    await kit.verified("rl@club.com")
    c = kit.browser()
    bad = {"email": "rl@club.com", "password": "wrong-password"}
    for _ in range(5):
        assert_problem(await c.post("/api/v1/auth/login", json=bad), 401, "INVALID_CREDENTIALS")
    r = await c.post("/api/v1/auth/login", json={"email": "rl@club.com", "password": PASSWORD})
    assert_problem(r, 429, "RATE_LIMITED")
    assert 0 < int(r.headers["retry-after"]) <= 900
    # other accounts from the same IP are unaffected (key = ip + email)
    await kit.verified("other@club.com")
    r = await c.post("/api/v1/auth/login", json={"email": "other@club.com", "password": PASSWORD})
    assert r.status_code == 200


async def test_refresh_rotation_and_reuse_detection(kit: Kit) -> None:
    c, _ = await kit.verified("rot@club.com")
    old = c.cookies.get("pv_refresh")
    r = await c.post("/api/v1/auth/refresh")
    assert r.status_code == 200
    new = c.cookies.get("pv_refresh")
    assert new and new != old
    assert (await kit.claims(c, r.json()))["sub"] == r.json()["user"]["id"]

    # attacker replays the rotated token: whole family revoked, including the legit new one
    thief = kit.browser()
    thief.cookies.set("pv_refresh", old or "", domain="id.test", path="/api/v1/auth")
    r = await thief.post("/api/v1/auth/refresh")
    assert_problem(r, 401, "UNAUTHENTICATED")
    assert "pv_refresh=" in r.headers["set-cookie"] and "max-age=0" in r.headers["set-cookie"].lower()
    assert_problem(await c.post("/api/v1/auth/refresh"), 401, "UNAUTHENTICATED")
    assert kit.db.execute("SELECT count(*) FROM refresh_tokens WHERE revoked_at IS NULL") == [(0,)]


async def test_refresh_without_cookie_or_expired(kit: Kit) -> None:
    assert_problem(await kit.browser().post("/api/v1/auth/refresh"), 401, "UNAUTHENTICATED")
    c, _ = await kit.verified("exp@club.com")
    kit.db.execute("UPDATE refresh_tokens SET expires_at = now() - interval '1 second'")
    assert_problem(await c.post("/api/v1/auth/refresh"), 401, "UNAUTHENTICATED")


async def test_logout_revokes_family_and_clears_cookie(kit: Kit) -> None:
    c, _ = await kit.verified("out@club.com")
    token = c.cookies.get("pv_refresh")
    r = await c.post("/api/v1/auth/logout")
    assert r.status_code == 204 and "max-age=0" in r.headers["set-cookie"].lower()
    replay = kit.browser()
    replay.cookies.set("pv_refresh", token or "", domain="id.test", path="/api/v1/auth")
    assert_problem(await replay.post("/api/v1/auth/refresh"), 401, "UNAUTHENTICATED")
    assert (await kit.browser().post("/api/v1/auth/logout")).status_code == 204  # idempotent, no cookie


async def test_forgot_and_reset_password_revokes_sessions(kit: Kit) -> None:
    c, _ = await kit.verified("fp@club.com")
    other_device, _ = await kit.verified("fp2@club.com")  # unrelated user keeps their session
    anon = kit.browser()
    assert (await anon.post("/api/v1/auth/password/forgot", json={"email": "ghost@club.com"})).status_code == 202
    assert kit.outbox.to("ghost@club.com") == []
    assert (await anon.post("/api/v1/auth/password/forgot", json={"email": "FP@club.com"})).status_code == 202
    mail = kit.outbox.to("fp@club.com")[-1]
    assert mail.link.startswith(f"{WEB}/reset-password?token=")
    token = kit.outbox.last_token("fp@club.com")

    assert_problem(
        await anon.post("/api/v1/auth/password/reset", json={"token": token, "password": "short"}),
        422,
        "VALIDATION_FAILED",
    )
    new_pw = "a brand new password"
    r = await anon.post("/api/v1/auth/password/reset", json={"token": token, "password": new_pw})
    assert r.status_code == 204
    assert_problem(await c.post("/api/v1/auth/refresh"), 401, "UNAUTHENTICATED")  # signed out everywhere
    assert (await other_device.post("/api/v1/auth/refresh")).status_code == 200
    again = await anon.post("/api/v1/auth/password/reset", json={"token": token, "password": new_pw})
    assert_problem(again, 410, "TOKEN_EXPIRED")
    old_login = await anon.post("/api/v1/auth/login", json={"email": "fp@club.com", "password": PASSWORD})
    assert_problem(old_login, 401, "INVALID_CREDENTIALS")
    r = await anon.post("/api/v1/auth/login", json={"email": "fp@club.com", "password": new_pw})
    assert r.status_code == 200


async def test_reset_token_expires_after_30_minutes(kit: Kit) -> None:
    await kit.verified("slow@club.com")
    anon = kit.browser()
    await anon.post("/api/v1/auth/password/forgot", json={"email": "slow@club.com"})
    rows = kit.db.execute(
        "SELECT expires_at - created_at FROM email_tokens WHERE purpose = 'reset_password' AND used_at IS NULL"
    )
    assert [r[0].total_seconds() for r in rows] == pytest.approx([1800], abs=5)
    kit.db.execute("UPDATE email_tokens SET expires_at = now() - interval '1 second' WHERE purpose = 'reset_password'")
    body = {"token": kit.outbox.last_token("slow@club.com"), "password": "whatever-long-enough"}
    assert_problem(await anon.post("/api/v1/auth/password/reset", json=body), 410, "TOKEN_EXPIRED")


async def test_forgot_is_rate_limited(kit: Kit) -> None:
    anon = kit.browser()
    for _ in range(5):
        assert (await anon.post("/api/v1/auth/password/forgot", json={"email": "x@club.com"})).status_code == 202
    assert_problem(await anon.post("/api/v1/auth/password/forgot", json={"email": "x@club.com"}), 429, "RATE_LIMITED")


async def test_access_token_claims_and_jwks(kit: Kit) -> None:
    c, session = await kit.verified("jwt@club.com")
    r = await c.get("/.well-known/jwks.json")
    assert r.status_code == 200 and "max-age" in r.headers["cache-control"]
    [jwk] = r.json()["keys"]
    assert jwk["kty"] == "RSA" and jwk["alg"] == "RS256" and jwk["use"] == "sig" and jwk["kid"]
    assert "d" not in jwk  # public only
    claims = await kit.claims(c, session)
    assert claims["sub"] == session["user"]["id"] and claims["email"] == "jwt@club.com"
    assert claims["email_verified"] is True
    assert claims["org"] is None and claims["role"] is None and claims["region"] is None
    assert claims["exp"] - claims["iat"] == 900 and len(claims["jti"]) == 32


async def test_me_and_bad_tokens(kit: Kit) -> None:
    c, session = await kit.verified("me@club.com", name="Me Myself")
    r = await c.get("/api/v1/me", headers=auth(session))
    assert r.status_code == 200
    me = r.json()
    assert me["name"] == "Me Myself" and me["email_verified"] is True and me["memberships"] == []
    assert_problem(await c.get("/api/v1/me"), 401, "UNAUTHENTICATED")
    assert_problem(await c.get("/api/v1/me", headers={"Authorization": "Bearer nope"}), 401, "UNAUTHENTICATED")
    forged = KeyRing(Settings(env="test", jwt_issuer="https://id.test")).sign(
        {"sub": me["id"], "aud": "pitchvision", "iss": "https://id.test", "iat": 1, "exp": 9999999999}
    )
    r = await c.get("/api/v1/me", headers={"Authorization": f"Bearer {forged}"})
    assert_problem(r, 401, "UNAUTHENTICATED")


async def test_delete_unverified_account(kit: Kit) -> None:
    c = kit.browser()
    session = (await kit.register(c, "typo@club.con")).json()
    r = await c.delete("/api/v1/me/unverified", headers=auth(session))
    assert r.status_code == 204 and "max-age=0" in r.headers["set-cookie"].lower()
    assert_problem(await c.get("/api/v1/me", headers=auth(session)), 401, "UNAUTHENTICATED")
    assert kit.db.execute("SELECT count(*) FROM users") == [(0,)]
    assert (await kit.register(c, "typo@club.con")).status_code == 201  # the address is free again

    v, vs = await kit.verified("solid@club.com")
    assert_problem(await v.delete("/api/v1/me/unverified", headers=auth(vs)), 409, "CONFLICT")


def test_settings_guards() -> None:
    with pytest.raises(ValidationError, match="JWT_PRIVATE_KEY"):
        Settings(env="production")
    with pytest.raises(ValidationError, match="COOKIE_SECURE"):
        Settings(env="staging", jwt_private_key_pem="x", cookie_secure=False)
    with pytest.raises(ValidationError, match="RESEND_API_KEY"):
        Settings(env="test", email_backend="resend")
    ring = KeyRing(Settings(env="test"))  # ephemeral key in test
    assert ring.jwks()["keys"][0]["kid"] == ring.kid
