import { http, HttpResponse, type HttpHandler } from 'msw';
import type {
  CreateOrgRequest,
  Membership,
  Org,
  RegisterRequest,
  Role,
  Session,
  User,
} from '@/api/identity';

/**
 * In-memory identity service following docs/contracts/identity-api.md. Used by the Vitest
 * suites (msw/node) and by the dev server (`VITE_MOCK_API=1`, msw/browser). The `pv_refresh`
 * HttpOnly cookie is simulated by `db.cookie`.
 */

export interface MockUser {
  id: string;
  name: string;
  email: string;
  password: string;
  email_verified: boolean;
  created_at: string;
  email_prefs?: { job_ready?: boolean; job_failed?: boolean; export_ready?: boolean };
}

export interface MockInvite {
  token: string;
  org_id: string;
  email: string;
  role: Role;
  inviter_name: string;
  expired: boolean;
  id?: string;
  revoked?: boolean;
}

export interface MockDb {
  users: MockUser[];
  orgs: Org[];
  memberships: { user_id: string; org_id: string; role: Role }[];
  invites: MockInvite[];
  /** token → 'valid' | 'expired' */
  resetTokens: Record<string, 'valid' | 'expired'>;
  /** token → user id */
  verifyTokens: Record<string, string>;
  /** Simulated `pv_refresh` cookie. */
  cookie: { userId: string; activeOrgId: string | null } | null;
  /** Access tokens the server now rejects with 401 (simulates expiry). */
  rejectedTokens: Set<string>;
  /** SCR-41 waitlist requests. */
  upgradeRequests: { org_id: string; user_id: string; plan: string; reason: string | null }[];
  /** Request log for assertions: "POST /api/v1/auth/refresh {\"org_id\":\"…\"}". */
  log: string[];
  seq: number;
}

const NOW = '2026-10-05T09:00:00Z';

export function createMockDb(partial: Partial<MockDb> = {}): MockDb {
  return {
    users: [],
    orgs: [],
    memberships: [],
    invites: [],
    resetTokens: {},
    verifyTokens: {},
    cookie: null,
    rejectedTokens: new Set(),
    upgradeRequests: [],
    log: [],
    seq: 0,
    ...partial,
  };
}

export function mockUser(over: Partial<MockUser> & { email: string }): MockUser {
  return {
    id: `u-${over.email.split('@')[0] ?? 'x'}`,
    name: 'Dong M.',
    password: 'correct horse battery',
    email_verified: true,
    created_at: NOW,
    ...over,
  };
}

export function mockOrg(over: Partial<Org> & { id: string }): Org {
  return { name: 'Riverside FC', kind: 'club', region: 'eu', created_at: NOW, ...over };
}

export function problem(status: number, code: string, extra: Record<string, unknown> = {}) {
  return HttpResponse.json(
    { type: 'about:blank', title: code, status, code, ...extra },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );
}

function publicUser(u: MockUser): User {
  return {
    id: u.id,
    name: u.name,
    email: u.email,
    email_verified: u.email_verified,
    created_at: u.created_at,
  };
}

function membershipsOf(db: MockDb, userId: string): Membership[] {
  return db.memberships
    .filter((m) => m.user_id === userId)
    .flatMap((m) => {
      const org = db.orgs.find((o) => o.id === m.org_id);
      return org ? [{ org_id: org.id, org_name: org.name, role: m.role, region: org.region }] : [];
    });
}

/** Issues a Session for the cookie's user (what refresh / login / register return). */
export function sessionFor(db: MockDb, userId: string, activeOrgId?: string | null): Session {
  const user = db.users.find((u) => u.id === userId);
  if (!user) throw new Error(`mock: unknown user ${userId}`);
  const memberships = membershipsOf(db, userId);
  const wanted = activeOrgId === undefined ? db.cookie?.activeOrgId : activeOrgId;
  const active =
    memberships.find((m) => m.org_id === wanted)?.org_id ?? memberships[0]?.org_id ?? null;
  db.cookie = { userId, activeOrgId: active };
  db.seq += 1;
  return {
    access_token: `mock.${userId}.${active ?? 'none'}.${db.seq}`,
    token_type: 'bearer',
    expires_in: 900,
    user: publicUser(user),
    memberships,
    active_org_id: active,
  };
}

function authUser(db: MockDb, request: Request): MockUser | null {
  const header = request.headers.get('Authorization') ?? '';
  const token = header.replace(/^Bearer\s+/i, '');
  if (!token.startsWith('mock.') || db.rejectedTokens.has(token)) return null;
  const userId = token.split('.')[1];
  return db.users.find((u) => u.id === userId) ?? null;
}

async function jsonBody<T>(request: Request): Promise<Partial<T>> {
  const text = await request.text();
  if (!text) return {};
  try {
    return JSON.parse(text) as Partial<T>;
  } catch {
    return {};
  }
}

const RANK: Record<Role, number> = { viewer: 0, editor: 1, admin: 2, owner: 3 };

function rankOf(db: MockDb, orgId: string, userId: string): number {
  const m = db.memberships.find((x) => x.org_id === orgId && x.user_id === userId);
  return m ? RANK[m.role] : -1;
}

function ownersOf(db: MockDb, orgId: string): number {
  return db.memberships.filter((m) => m.org_id === orgId && m.role === 'owner').length;
}

/** Handlers for every identity endpoint in the contract (matched on any origin). */
export function identityHandlers(db: MockDb): HttpHandler[] {
  const meOf = (user: MockUser) => ({
    ...publicUser(user),
    memberships: membershipsOf(db, user.id),
    email_prefs: {
      job_ready: user.email_prefs?.job_ready ?? true,
      job_failed: user.email_prefs?.job_failed ?? true,
      export_ready: user.email_prefs?.export_ready ?? true,
    },
  });
  const log = async (request: Request) => {
    const body = await request.clone().text();
    db.log.push(`${request.method} ${new URL(request.url).pathname}${body ? ` ${body}` : ''}`);
  };

  return [
    http.post('*/api/v1/auth/register', async ({ request }) => {
      await log(request);
      const body = await jsonBody<RegisterRequest>(request);
      const email = (body.email ?? '').toLowerCase();
      if (!body.name || !email || (body.password ?? '').length < 10) {
        return problem(422, 'VALIDATION_FAILED');
      }
      if (db.users.some((u) => u.email === email)) return problem(409, 'EMAIL_TAKEN');
      const invite = db.invites.find((i) => i.token === body.invite_token && !i.expired);
      const user = mockUser({
        email,
        name: body.name,
        password: body.password ?? '',
        email_verified: invite?.email === email,
      });
      db.users.push(user);
      db.verifyTokens[`verify-${user.id}`] = user.id; // the "email link" (dev / e2e: /verify-email?token=verify-<id>)
      if (invite && invite.email === email) {
        db.memberships.push({ user_id: user.id, org_id: invite.org_id, role: invite.role });
        return HttpResponse.json(sessionFor(db, user.id, invite.org_id), { status: 201 });
      }
      return HttpResponse.json(sessionFor(db, user.id, null), { status: 201 });
    }),

    http.post('*/api/v1/auth/login', async ({ request }) => {
      await log(request);
      const body = await jsonBody<{ email: string; password: string }>(request);
      const user = db.users.find(
        (u) => u.email === (body.email ?? '').toLowerCase() && u.password === body.password,
      );
      if (!user) return problem(401, 'INVALID_CREDENTIALS');
      return HttpResponse.json(sessionFor(db, user.id));
    }),

    http.post('*/api/v1/auth/refresh', async ({ request }) => {
      await log(request);
      if (!db.cookie) return problem(401, 'UNAUTHENTICATED');
      const body = await jsonBody<{ org_id: string }>(request);
      const userId = db.cookie.userId;
      if (body.org_id && !membershipsOf(db, userId).some((m) => m.org_id === body.org_id)) {
        return problem(403, 'FORBIDDEN');
      }
      return HttpResponse.json(sessionFor(db, userId, body.org_id ?? db.cookie.activeOrgId));
    }),

    http.post('*/api/v1/auth/logout', async ({ request }) => {
      await log(request);
      db.cookie = null;
      return new HttpResponse(null, { status: 204 });
    }),

    http.post('*/api/v1/auth/verify-email/resend', async ({ request }) => {
      await log(request);
      if (!authUser(db, request)) return problem(401, 'UNAUTHENTICATED');
      return new HttpResponse(null, { status: 202 });
    }),

    http.post('*/api/v1/auth/verify-email', async ({ request }) => {
      await log(request);
      const { token = '' } = await jsonBody<{ token: string }>(request);
      const userId = db.verifyTokens[token];
      const user = db.users.find((u) => u.id === userId);
      if (!user) return problem(410, 'TOKEN_EXPIRED');
      db.verifyTokens = Object.fromEntries(
        Object.entries(db.verifyTokens).filter(([k]) => k !== token),
      );
      user.email_verified = true;
      return HttpResponse.json(sessionFor(db, user.id));
    }),

    http.post('*/api/v1/auth/password/forgot', async ({ request }) => {
      await log(request);
      return new HttpResponse(null, { status: 202 });
    }),

    http.post('*/api/v1/auth/password/reset', async ({ request }) => {
      await log(request);
      const { token = '', password = '' } = await jsonBody<{ token: string; password: string }>(
        request,
      );
      if (db.resetTokens[token] !== 'valid') return problem(410, 'TOKEN_EXPIRED');
      if (password.length < 10) return problem(422, 'VALIDATION_FAILED');
      db.resetTokens[token] = 'expired';
      db.cookie = null;
      return new HttpResponse(null, { status: 204 });
    }),

    http.get('*/api/v1/me', ({ request }) => {
      const user = authUser(db, request);
      if (!user) return problem(401, 'UNAUTHENTICATED');
      return HttpResponse.json(meOf(user));
    }),

    http.patch('*/api/v1/me', async ({ request }) => {
      await log(request);
      const user = authUser(db, request);
      if (!user) return problem(401, 'UNAUTHENTICATED');
      const body = await jsonBody<{ name: string; email_prefs: MockUser['email_prefs'] }>(request);
      if (body.name !== undefined) {
        const name = body.name.trim();
        if (!name) return problem(422, 'VALIDATION_FAILED');
        user.name = name;
      }
      if (body.email_prefs) user.email_prefs = { ...user.email_prefs, ...body.email_prefs };
      return HttpResponse.json(meOf(user));
    }),

    http.post('*/api/v1/auth/password/change', async ({ request }) => {
      await log(request);
      const user = authUser(db, request);
      if (!user) return problem(401, 'UNAUTHENTICATED');
      const body = await jsonBody<{ current_password: string; new_password: string }>(request);
      if (body.current_password !== user.password) return problem(401, 'INVALID_CREDENTIALS');
      if ((body.new_password ?? '').length < 10) return problem(422, 'VALIDATION_FAILED');
      user.password = body.new_password ?? user.password;
      return new HttpResponse(null, { status: 204 });
    }),

    http.post('*/api/v1/auth/sessions/revoke-others', async ({ request }) => {
      await log(request);
      if (!authUser(db, request)) return problem(401, 'UNAUTHENTICATED');
      return new HttpResponse(null, { status: 204 });
    }),

    http.get<{ id: string }>('*/api/v1/orgs/:id/members', ({ request, params }) => {
      const user = authUser(db, request);
      if (!user) return problem(401, 'UNAUTHENTICATED');
      if (rankOf(db, params.id, user.id) < 2) return problem(403, 'FORBIDDEN');
      return HttpResponse.json(
        db.memberships
          .filter((m) => m.org_id === params.id)
          .flatMap((m) => {
            const u = db.users.find((x) => x.id === m.user_id);
            return u
              ? [{ user_id: u.id, name: u.name, email: u.email, role: m.role, joined_at: NOW }]
              : [];
          }),
      );
    }),

    http.patch<{ id: string; userId: string }>(
      '*/api/v1/orgs/:id/members/:userId',
      async ({ request, params }) => {
        await log(request);
        const user = authUser(db, request);
        if (!user) return problem(401, 'UNAUTHENTICATED');
        const m = db.memberships.find((x) => x.org_id === params.id && x.user_id === params.userId);
        if (!m) return problem(404, 'NOT_FOUND');
        const body = await jsonBody<{ role: Role }>(request);
        if (!body.role) return problem(422, 'VALIDATION_FAILED');
        if (m.role === 'owner' && body.role !== 'owner' && ownersOf(db, params.id) <= 1) {
          return problem(409, 'LAST_OWNER');
        }
        m.role = body.role;
        const u = db.users.find((x) => x.id === m.user_id);
        return HttpResponse.json({
          user_id: m.user_id,
          name: u?.name ?? '',
          email: u?.email ?? '',
          role: m.role,
          joined_at: NOW,
        });
      },
    ),

    http.delete<{ id: string; userId: string }>(
      '*/api/v1/orgs/:id/members/:userId',
      async ({ request, params }) => {
        await log(request);
        if (!authUser(db, request)) return problem(401, 'UNAUTHENTICATED');
        const m = db.memberships.find((x) => x.org_id === params.id && x.user_id === params.userId);
        if (!m) return problem(404, 'NOT_FOUND');
        if (m.role === 'owner' && ownersOf(db, params.id) <= 1) return problem(409, 'LAST_OWNER');
        db.memberships = db.memberships.filter((x) => x !== m);
        return new HttpResponse(null, { status: 204 });
      },
    ),

    http.get<{ id: string }>('*/api/v1/orgs/:id/invites', ({ request, params }) => {
      if (!authUser(db, request)) return problem(401, 'UNAUTHENTICATED');
      return HttpResponse.json(
        db.invites
          .filter((i) => i.org_id === params.id && !i.revoked)
          .map((i) => ({
            id: i.id ?? i.token,
            email: i.email,
            role: i.role,
            expires_at: i.expired ? '2026-01-01T00:00:00Z' : '2099-01-01T00:00:00Z',
            created_at: NOW,
            invited_by: null,
          })),
      );
    }),

    http.post<{ id: string }>('*/api/v1/orgs/:id/invites', async ({ request, params }) => {
      await log(request);
      const user = authUser(db, request);
      if (!user) return problem(401, 'UNAUTHENTICATED');
      const body = await jsonBody<{ emails: string[]; role: Role }>(request);
      const out = (body.emails ?? []).map((email) => {
        db.seq += 1;
        const inv: MockInvite = {
          id: `inv-${String(db.seq)}`,
          token: `tok-${String(db.seq)}`,
          org_id: params.id,
          email,
          role: body.role ?? 'viewer',
          inviter_name: user.name,
          expired: false,
        };
        db.invites.push(inv);
        return {
          id: inv.id,
          email,
          role: inv.role,
          expires_at: '2099-01-01T00:00:00Z',
          created_at: NOW,
          invited_by: user.id,
        };
      });
      return HttpResponse.json(out, { status: 201 });
    }),

    http.post<{ id: string; inviteId: string }>(
      '*/api/v1/orgs/:id/invites/:inviteId/resend',
      async ({ request }) => {
        await log(request);
        return new HttpResponse(null, { status: 202 });
      },
    ),

    http.delete<{ id: string; inviteId: string }>(
      '*/api/v1/orgs/:id/invites/:inviteId',
      async ({ request, params }) => {
        await log(request);
        const inv = db.invites.find((i) => (i.id ?? i.token) === params.inviteId);
        if (inv) inv.revoked = true;
        return new HttpResponse(null, { status: 204 });
      },
    ),

    http.post<{ id: string }>('*/api/v1/orgs/:id/upgrade-requests', async ({ request, params }) => {
      await log(request);
      const user = authUser(db, request);
      if (!user) return problem(401, 'UNAUTHENTICATED');
      const body = await jsonBody<{ plan: string; period: string; reason: string | null }>(request);
      const owner = rankOf(db, params.id, user.id) === 3;
      db.upgradeRequests.push({
        org_id: params.id,
        user_id: user.id,
        plan: body.plan ?? 'pro',
        reason: body.reason ?? null,
      });
      return HttpResponse.json(
        {
          id: `up-${String(db.upgradeRequests.length)}`,
          plan: body.plan ?? 'pro',
          period: body.period ?? 'monthly',
          owners_notified: owner ? 0 : ownersOf(db, params.id),
          created_at: NOW,
        },
        { status: 202 },
      );
    }),

    http.delete('*/api/v1/me/unverified', async ({ request }) => {
      await log(request);
      const user = authUser(db, request);
      if (!user) return problem(401, 'UNAUTHENTICATED');
      if (user.email_verified) return problem(409, 'CONFLICT');
      db.users = db.users.filter((u) => u.id !== user.id);
      db.cookie = null;
      return new HttpResponse(null, { status: 204 });
    }),

    http.post('*/api/v1/orgs', async ({ request }) => {
      await log(request);
      const user = authUser(db, request);
      if (!user) return problem(401, 'UNAUTHENTICATED');
      const body = await jsonBody<CreateOrgRequest>(request);
      if (!body.name || !body.kind || !body.region) return problem(422, 'VALIDATION_FAILED');
      const org = mockOrg({
        id: `org-${db.orgs.length + 1}`,
        name: body.name,
        kind: body.kind,
        region: body.region,
      });
      db.orgs.push(org);
      db.memberships.push({ user_id: user.id, org_id: org.id, role: 'owner' });
      return HttpResponse.json(org, { status: 201 });
    }),

    http.get<{ id: string }>('*/api/v1/orgs/:id', ({ request, params }) => {
      if (!authUser(db, request)) return problem(401, 'UNAUTHENTICATED');
      const org = db.orgs.find((o) => o.id === params.id);
      return org ? HttpResponse.json(org) : problem(404, 'NOT_FOUND');
    }),

    http.patch<{ id: string }>('*/api/v1/orgs/:id', async ({ request, params }) => {
      await log(request);
      if (!authUser(db, request)) return problem(401, 'UNAUTHENTICATED');
      const org = db.orgs.find((o) => o.id === params.id);
      if (!org) return problem(404, 'NOT_FOUND');
      const body = await jsonBody<{ name: string }>(request);
      if (body.name) org.name = body.name;
      return HttpResponse.json(org);
    }),

    http.get<{ token: string }>('*/api/v1/invites/:token', ({ params }) => {
      const invite = db.invites.find((i) => i.token === params.token);
      const org = db.orgs.find((o) => o.id === invite?.org_id);
      if (!invite || !org) return problem(404, 'NOT_FOUND');
      return HttpResponse.json({
        org_name: org.name,
        inviter_name: invite.inviter_name,
        email: invite.email,
        role: invite.role,
        region: org.region,
        expired: invite.expired,
      });
    }),

    http.post<{ token: string }>('*/api/v1/invites/:token/accept', async ({ request, params }) => {
      await log(request);
      const user = authUser(db, request);
      if (!user) return problem(401, 'UNAUTHENTICATED');
      const invite = db.invites.find((i) => i.token === params.token);
      if (!invite) return problem(404, 'NOT_FOUND');
      if (invite.expired) return problem(410, 'TOKEN_EXPIRED');
      if (invite.email !== user.email) return problem(403, 'INVITE_EMAIL_MISMATCH');
      if (!db.memberships.some((m) => m.user_id === user.id && m.org_id === invite.org_id)) {
        db.memberships.push({ user_id: user.id, org_id: invite.org_id, role: invite.role });
      }
      return HttpResponse.json(sessionFor(db, user.id, invite.org_id));
    }),
  ];
}
