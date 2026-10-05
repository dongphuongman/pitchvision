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
