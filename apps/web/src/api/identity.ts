import createClient from 'openapi-fetch';
import { createAuthFetch, throwOnError } from './http';
// Swap point: when apps/identity/openapi.json lands, `pnpm gen` writes ./generated/identity.ts
// with the same `paths` / `components` shape — change this one import and delete the stand-in.
import type { components, paths } from './identity-contract';

/**
 * Typed client for the global identity service (docs/contracts/identity-api.md).
 * The access token lives in memory only (src/auth/session.ts); the refresh token is the
 * HttpOnly `pv_refresh` cookie, hence `credentials: 'include'` on every call.
 */

type Schemas = components['schemas'];
export type Role = Schemas['Role'];
export type Region = Schemas['Region'];
export type OrgKind = Schemas['OrgKind'];
export type User = Schemas['User'];
export type Membership = Schemas['Membership'];
export type Session = Schemas['Session'];
export type Me = Schemas['Me'];
export type Org = Schemas['Org'];
export type InvitePreview = Schemas['InvitePreview'];
export type RegisterRequest = Schemas['RegisterRequest'];
export type CreateOrgRequest = Schemas['CreateOrgRequest'];
export type Member = Schemas['Member'];
export type Invite = Schemas['Invite'];
export type EmailPrefs = Schemas['EmailPrefs'];
export type UpgradeRequest = Schemas['UpgradeRequest'];
export type UpgradeRequestOut = Schemas['UpgradeRequestOut'];
export type DeletionPreview = Schemas['DeletionPreview'];
export type AnalysisDefaults = Schemas['AnalysisDefaults'];

/** GET /me (email prefs, analysis defaults); one cache entry shared by Settings and SCR-11. */
export const ME_KEY = ['identity', 'me'] as const;
export type InviteRole = Schemas['CreateInvitesRequest']['role'];

export const ORG_KINDS: readonly OrgKind[] = [
  'club',
  'academy',
  'media',
  'agency',
  'individual',
  'other',
];
export const REGIONS: readonly Region[] = ['eu', 'us'];

/** `VITE_IDENTITY_URL` without trailing slash; empty = same origin (nginx routes /api/v1/auth…). */
export const IDENTITY_BASE_URL = (import.meta.env.VITE_IDENTITY_URL ?? '').replace(/\/+$/, '');

function identityOrigin(baseUrl: string): string {
  return baseUrl || globalThis.location.origin;
}

export function createIdentityClient(baseUrl = IDENTITY_BASE_URL, fetchImpl?: typeof fetch) {
  const client = createClient<paths>({
    baseUrl: identityOrigin(baseUrl),
    credentials: 'include',
    headers: { Accept: 'application/json, application/problem+json' },
    fetch: createAuthFetch(() => fetchImpl ?? globalThis.fetch),
  });
  client.use(throwOnError);
  return client;
}

export type IdentityClient = ReturnType<typeof createIdentityClient>;

const client = createIdentityClient();

function required<T>(data: T | undefined, what: string): T {
  if (data === undefined) throw new Error(`Empty identity response: ${what}`);
  return data;
}

/** Endpoint functions; all throw ApiError (problem+json `code`) on non-2xx. */
export const identityApi = {
  async register(body: RegisterRequest): Promise<Session> {
    const { data } = await client.POST('/api/v1/auth/register', { body });
    return required(data, 'register');
  },
  async login(email: string, password: string): Promise<Session> {
    const { data } = await client.POST('/api/v1/auth/login', { body: { email, password } });
    return required(data, 'login');
  },
  /** Rotates the `pv_refresh` cookie; `orgId` switches the active org. */
  async refresh(orgId?: string): Promise<Session> {
    const { data } = await client.POST('/api/v1/auth/refresh', {
      body: orgId ? { org_id: orgId } : undefined,
    });
    return required(data, 'refresh');
  },
  async logout(): Promise<void> {
    await client.POST('/api/v1/auth/logout');
  },
  async verifyEmail(token: string): Promise<Session> {
    const { data } = await client.POST('/api/v1/auth/verify-email', { body: { token } });
    return required(data, 'verify-email');
  },
  async resendVerification(): Promise<void> {
    await client.POST('/api/v1/auth/verify-email/resend');
  },
  async forgotPassword(email: string): Promise<void> {
    await client.POST('/api/v1/auth/password/forgot', { body: { email } });
  },
  async resetPassword(token: string, password: string): Promise<void> {
    await client.POST('/api/v1/auth/password/reset', { body: { token, password } });
  },
  async me(signal?: AbortSignal): Promise<Me> {
    const { data } = await client.GET('/api/v1/me', { signal });
    return required(data, 'me');
  },
  /** SCR-40 D03 / DLG-08: orgs that block deleting the account, orgs deleted with it. */
  async deletionPreview(signal?: AbortSignal): Promise<DeletionPreview> {
    const { data } = await client.GET('/api/v1/me/deletion-preview', { signal });
    return required(data, 'deletion preview');
  },
  /** SCR-40 D03: 409 LAST_OWNER while you are an org's only owner; clears the refresh cookie. */
  async deleteAccount(confirmEmail: string): Promise<void> {
    await client.DELETE('/api/v1/me', { body: { confirm_email: confirmEmail } });
  },
  /** SCR-40 D04 (owner): `confirmName` must equal the org's name. */
  async deleteOrg(id: string, confirmName: string): Promise<void> {
    await client.DELETE('/api/v1/orgs/{id}', {
      params: { path: { id } },
      body: { confirm_name: confirmName },
    });
  },
  async deleteUnverified(): Promise<void> {
    await client.DELETE('/api/v1/me/unverified');
  },
  async createOrg(body: CreateOrgRequest): Promise<Org> {
    const { data } = await client.POST('/api/v1/orgs', { body });
    return required(data, 'create org');
  },
  async updateOrg(id: string, name: string): Promise<Org> {
    const { data } = await client.PATCH('/api/v1/orgs/{id}', {
      params: { path: { id } },
      body: { name },
    });
    return required(data, 'update org');
  },
  async updateMe(body: Schemas['UpdateMeRequest']): Promise<Me> {
    const { data } = await client.PATCH('/api/v1/me', { body });
    return required(data, 'update me');
  },
  async changePassword(currentPassword: string, newPassword: string): Promise<void> {
    await client.POST('/api/v1/auth/password/change', {
      body: { current_password: currentPassword, new_password: newPassword },
    });
  },
  async signOutOthers(): Promise<void> {
    await client.POST('/api/v1/auth/sessions/revoke-others');
  },
  async getOrg(id: string, signal?: AbortSignal): Promise<Org> {
    const { data } = await client.GET('/api/v1/orgs/{id}', { params: { path: { id } }, signal });
    return required(data, 'org');
  },
  async members(id: string, signal?: AbortSignal): Promise<Member[]> {
    const { data } = await client.GET('/api/v1/orgs/{id}/members', {
      params: { path: { id } },
      signal,
    });
    return required(data, 'members');
  },
  async setMemberRole(id: string, userId: string, role: Role): Promise<Member> {
    const { data } = await client.PATCH('/api/v1/orgs/{id}/members/{user_id}', {
      params: { path: { id, user_id: userId } },
      body: { role },
    });
    return required(data, 'member');
  },
  async removeMember(id: string, userId: string): Promise<void> {
    await client.DELETE('/api/v1/orgs/{id}/members/{user_id}', {
      params: { path: { id, user_id: userId } },
    });
  },
  async invites(id: string, signal?: AbortSignal): Promise<Invite[]> {
    const { data } = await client.GET('/api/v1/orgs/{id}/invites', {
      params: { path: { id } },
      signal,
    });
    return required(data, 'invites');
  },
  async invite(
    id: string,
    emails: string[],
    role: InviteRole,
    message?: string,
  ): Promise<Invite[]> {
    const { data } = await client.POST('/api/v1/orgs/{id}/invites', {
      params: { path: { id } },
      body: { emails, role, ...(message ? { message } : {}) },
    });
    return required(data, 'invite');
  },
  async resendInvite(id: string, inviteId: string): Promise<void> {
    await client.POST('/api/v1/orgs/{id}/invites/{invite_id}/resend', {
      params: { path: { id, invite_id: inviteId } },
    });
  },
  async revokeInvite(id: string, inviteId: string): Promise<void> {
    await client.DELETE('/api/v1/orgs/{id}/invites/{invite_id}', {
      params: { path: { id, invite_id: inviteId } },
    });
  },
  async requestUpgrade(id: string, body: UpgradeRequest): Promise<UpgradeRequestOut> {
    const { data } = await client.POST('/api/v1/orgs/{id}/upgrade-requests', {
      params: { path: { id } },
      body,
    });
    return required(data, 'upgrade request');
  },
  async getInvite(token: string, signal?: AbortSignal): Promise<InvitePreview> {
    const { data } = await client.GET('/api/v1/invites/{token}', {
      params: { path: { token } },
      signal,
    });
    return required(data, 'invite');
  },
  async acceptInvite(token: string): Promise<Session> {
    const { data } = await client.POST('/api/v1/invites/{token}/accept', {
      params: { path: { token } },
    });
    return required(data, 'accept invite');
  },
};

/**
 * Full-page navigation target for "Continue with Google". The identity service redirects to
 * Google and, after the callback, back to the web app at `/auth/callback#…` with the
 * `pv_refresh` cookie set. `returnTo` must already be sanitised (safeReturnTo).
 */
export function googleStartUrl(returnTo?: string, baseUrl = IDENTITY_BASE_URL): string {
  const url = new URL('/api/v1/auth/oidc/google/start', identityOrigin(baseUrl));
  if (returnTo) url.searchParams.set('return_to', returnTo);
  return baseUrl ? url.toString() : `${url.pathname}${url.search}`;
}
