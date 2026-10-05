# Identity & org API contract (M1-BE-05/06, M1-FE-04..06)

Source of truth for the identity service (`apps/identity`, package `pitchvision_identity`), the regional
API's JWT check (`apps/api`) and the web client. Change it in the same PR as the code.

## Topology
- identity-svc = global control plane (AD §4, §9.4). Own Postgres DB (`PV_IDENTITY_DATABASE_URL`), own Alembic.
  Tables: users, orgs, memberships, invites, refresh_tokens, email_tokens (verify / reset), oauth_accounts.
  No video or player data.
- Self-host / local: same image as api (`command: identity`), served behind the web nginx on the same origin.
  Path ownership on the shared origin:
  - identity: `/api/v1/auth/*`, `/api/v1/me`, `/api/v1/orgs*`, `/api/v1/invites/*`, `/.well-known/jwks.json`
  - api (regional): everything else under `/api/v1/*`
  - identity's own OpenAPI/docs therefore live at `/api/v1/auth/openapi.json` / `/api/v1/auth/docs`
    (committed as `apps/identity/openapi.json`); probes `/healthz`, `/readyz` are not proxied.
- Web reads `VITE_IDENTITY_URL` and `VITE_API_URL` (both default to same origin).

## Tokens
- Access token: JWT **RS256**, 15 min, `kid` header, JWKS at `GET /.well-known/jwks.json`.
  Claims: `iss`=`PV_JWT_ISSUER`, `aud`=`pitchvision`, `sub` (user uuid), `email`, `email_verified` (bool),
  `org` (active org uuid or null), `role` (`owner|admin|editor|viewer` or null), `region` (`eu|us` or null), `iat`, `exp`, `jti`.
- Sent as `Authorization: Bearer <token>`. Web keeps it in memory only.
- Refresh token: opaque, HttpOnly + Secure + SameSite=Lax cookie `pv_refresh`, path `/api/v1/auth`, 30 days,
  rotated on every use; reuse of a rotated token revokes the whole family (AD §14).
- Password reset success → revoke all refresh tokens of the user.
- Which org is active: `{org_id}` on refresh, else the session's org, else the user's last active org, else the
  first membership — always re-checked against current memberships (a removed member silently falls back).
- A 401 from `/auth/refresh` also expires the `pv_refresh` cookie (`Max-Age=0`).
- `PV_COOKIE_SECURE=false` exists only for plain-http local setups (Safari ignores Secure cookies on
  http://localhost); identity refuses to start with it outside `PV_ENV=local|test`.
- Signing key: `PV_JWT_PRIVATE_KEY_PEM` or `PV_JWT_PRIVATE_KEY_FILE` (RSA ≥ 2048), `kid` = `PV_JWT_KID` or the
  RFC 7638 thumbprint. Without one, local/test generate an ephemeral key (warning; tokens die on restart);
  staging/production fail fast.

## Rate limits (per client IP + email, 429 `RATE_LIMITED` with `Retry-After`)
- login: 5 failed attempts / 15 min (a success resets the counter; UIUX SCR-02 lock)
- password/forgot: 5 / 15 min · verify-email/resend: 3 / 10 min
- In-memory per process for now (interface ready for Redis).

## Endpoints (all JSON, errors = RFC 9457 problem+json with `code` from `pitchvision_schemas.ErrorCode`)
| Method & path | Body | Success | Notes |
|---|---|---|---|
| POST /api/v1/auth/register | `{name, email, password, invite_token?, plan?}` | 201 `Session` | password ≥ 10 chars; email exists → 409 `EMAIL_TAKEN`; invite with matching email → `email_verified=true` and membership created; otherwise verification email sent |
| POST /api/v1/auth/login | `{email, password}` | 200 `Session` | bad creds → 401 `INVALID_CREDENTIALS` (same for unknown email) |
| POST /api/v1/auth/refresh | — (cookie) | 200 `Session` | optional body `{org_id}` switches active org |
| POST /api/v1/auth/logout | — | 204 | revokes current refresh family, clears cookie |
| POST /api/v1/auth/verify-email | `{token}` | 200 `Session` | token 24 h, single use; expired/used/unknown → 410 `TOKEN_EXPIRED`; a new link invalidates older ones |
| POST /api/v1/auth/verify-email/resend | — (auth) | 202 | rate limited; no-op if already verified |
| POST /api/v1/auth/password/forgot | `{email}` | 202 | always 202 (no account enumeration); link valid 30 min |
| POST /api/v1/auth/password/reset | `{token, password}` | 204 | expired/used/unknown → 410 `TOKEN_EXPIRED`; also marks the email verified |
| GET /api/v1/auth/oidc/google/start?return_to= | — | 302 to Google | state + PKCE + nonce |
| GET /api/v1/auth/oidc/google/callback | — | 302 to web `/auth/callback#…` | Google-verified email ⇒ email_verified; links to existing user by verified email (see OIDC below) |
| GET /api/v1/me | — | 200 `Me` | |
| DELETE /api/v1/me/unverified | — | 204 | "Change it" on SCR-06: deletes an unverified account and clears the cookie; verified → 409 `CONFLICT` |
| POST /api/v1/orgs | `{name, kind, my_role_title?, region}` | 201 `Org` | creator = owner; switch to it via refresh |
| GET /api/v1/orgs/{id} | — | 200 `Org` | member only |
| PATCH /api/v1/orgs/{id} | `{name?}` | 200 `Org` | admin+; region is immutable |
| GET /api/v1/orgs/{id}/members | — | 200 `Member[]` | admin+ |
| PATCH /api/v1/orgs/{id}/members/{user_id} | `{role}` | 200 `Member` | admin+; cannot demote the last owner (409 `LAST_OWNER`) |
| DELETE /api/v1/orgs/{id}/members/{user_id} | — | 204 | admin+; same last-owner rule |
| POST /api/v1/orgs/{id}/invites | `{emails: string[], role, message?}` | 201 `Invite[]` | admin+; role ∈ viewer/editor/admin; 7-day expiry |
| GET /api/v1/orgs/{id}/invites | — | 200 `Invite[]` | admin+, pending only |
| POST /api/v1/orgs/{id}/invites/{invite_id}/resend · DELETE …/{invite_id} | — | 202 · 204 | admin+ |
| GET /api/v1/invites/{token} | — | 200 `InvitePreview` | public: `{org_name, inviter_name, email, role, expired}` |
| POST /api/v1/invites/{token}/accept | — (auth) | 200 `Session` | email must match → else 403 `INVITE_EMAIL_MISMATCH` |

Shapes:
- `Session` = `{access_token, token_type:"bearer", expires_in, user: User, memberships: Membership[], active_org_id}` (+ sets cookie)
- `User` = `{id, name, email, email_verified, created_at}`
- `Membership` = `{org_id, org_name, role, region}`
- `Me` = `User & {memberships}`
- `Org` = `{id, name, kind, region, created_at}`; `kind` ∈ `club|academy|media|agency|individual|other`
- `Member` = `{user_id, name, email, role, joined_at}`; `Invite` = `{id, email, role, expires_at, created_at, invited_by}`

New error codes to add to `pitchvision_schemas.ErrorCode`: `EMAIL_TAKEN`, `INVALID_CREDENTIALS`,
`EMAIL_NOT_VERIFIED`, `TOKEN_EXPIRED`, `LAST_OWNER`, `INVITE_EMAIL_MISMATCH`, `REGION_MISMATCH`.

## Regional API side (apps/api)
- Verifies RS256 with JWKS (`PV_JWKS_URL`, cached, refetch on unknown `kid`), checks iss/aud/exp, requires
  `email_verified` and `org`. `region` claim must equal `PV_REGION` → else 409 `REGION_MISMATCH`.
- Exposes `Principal{user_id, org_id, role, region}`; `require_role("editor")` dependency (viewer < editor < admin < owner).
- The local dev header `X-Dev-Org` stub stays only for `PV_ENV=local` tests and is removed from the default path.

## Google OIDC
- `start`: 404 `NOT_FOUND` when `PV_GOOGLE_CLIENT_ID/SECRET` are unset. `return_to` must be a same-origin path
  (`/…`, not `//…`), otherwise it is dropped. State, nonce and the PKCE verifier ride in an HttpOnly cookie
  `pv_oidc` (path `/api/v1/auth/oidc`, 10 min) signed with the identity key.
- `callback` redirects to `{PV_WEB_URL}/auth/callback#status=ok&new_user=true|false[&return_to=/…]` with the
  `pv_refresh` cookie set — the web then calls `POST /auth/refresh` to get the Session — or to
  `…/auth/callback#error=<ErrorCode>` (`UNAUTHENTICATED` for cancel / bad state / bad nonce / bad id_token,
  `EMAIL_NOT_VERIFIED` when Google says the email is unverified). No token ever appears in a URL.
- Redirect URI registered at Google: `{PV_PUBLIC_URL or PV_WEB_URL}/api/v1/auth/oidc/google/callback`.
- Linking to an existing *unverified* password account removes that password and its sessions (whoever set it
  never proved the address).

## Email
- Port `EmailSender` with adapters: `console` (log the link, default local), `smtp` (Mailpit in compose), `resend` (prod).
- Links point at the web: `/verify-email?token=`, `/reset-password?token=`, `/invite/{token}`.
- Delivery failures are logged and never fail the request (the user can resend). Only SHA-256 hashes of
  email/invite/refresh tokens are stored.
