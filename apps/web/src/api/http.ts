import type { Middleware } from 'openapi-fetch';
import { ApiError } from './errors';

/**
 * Shared transport pieces for every openapi-fetch client (regional API + identity service).
 */

/**
 * Every non-2xx response becomes a thrown ApiError (problem+json parsed), so callers and
 * TanStack Query only ever see `data` on success and a typed error on failure.
 */
export const throwOnError: Middleware = {
  async onResponse({ response }) {
    if (!response.ok) throw await ApiError.fromResponse(response);
    return undefined;
  },
  onError({ error }) {
    if (error instanceof ApiError) return error;
    return ApiError.network(error);
  },
};

/**
 * Seam between the transport and the auth session (registered by `src/auth/session.ts`), so the
 * HTTP layer never imports the store (no import cycle) and tests can plug in fakes.
 */
export interface AuthHooks {
  /** Current in-memory access token (never persisted). */
  getAccessToken: () => string | null;
  /** Single-flight refresh; resolves to the new access token, or null when signed out. */
  refresh: () => Promise<string | null>;
}

let authHooks: AuthHooks = {
  getAccessToken: () => null,
  refresh: () => Promise.resolve(null),
};

export function setAuthHooks(hooks: AuthHooks): void {
  authHooks = hooks;
}

/**
 * Identity endpoints that must never trigger "refresh then retry": they *are* the session
 * lifecycle (a 401 from login means bad credentials, from refresh means signed out).
 */
const NO_RETRY_PATHS = [
  '/api/v1/auth/login',
  '/api/v1/auth/register',
  '/api/v1/auth/refresh',
  '/api/v1/auth/logout',
  '/api/v1/auth/verify-email',
  '/api/v1/auth/password/',
];

function skipRetry(url: string): boolean {
  const path = new URL(url).pathname.replace(/\/+$/, '');
  return NO_RETRY_PATHS.some((p) => (p.endsWith('/') ? path.startsWith(p) : path === p));
}

/**
 * fetch wrapper: attaches `Authorization: Bearer <access token>`; on 401 it refreshes the
 * session once (single flight, shared by concurrent callers) and retries the request once.
 * `getFetch` is resolved per call so tests / MSW can swap `globalThis.fetch`.
 */
export function createAuthFetch(getFetch: () => typeof fetch) {
  return async function authFetch(request: Request): Promise<Response> {
    // Clone before the body is consumed so the request can be replayed after a refresh.
    const replay = request.clone();
    const token = authHooks.getAccessToken();
    if (token && !request.headers.has('Authorization')) {
      request.headers.set('Authorization', `Bearer ${token}`);
    }
    const response = await getFetch()(request);
    if (response.status !== 401 || !token || skipRetry(request.url)) return response;

    // Another caller may already have refreshed while this request was in flight.
    const current = authHooks.getAccessToken();
    const fresh = current && current !== token ? current : await authHooks.refresh();
    if (!fresh) return response;
    replay.headers.set('Authorization', `Bearer ${fresh}`);
    return getFetch()(replay);
  };
}
