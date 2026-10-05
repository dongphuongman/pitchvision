import createClient from 'openapi-fetch';
import type { paths } from './generated/openapi';
import { createAuthFetch, throwOnError } from './http';

/** `VITE_API_URL` without trailing slash; empty = same origin (dev proxy / same-host deploy). */
export const API_BASE_URL = (import.meta.env.VITE_API_URL ?? '').replace(/\/+$/, '');

export function createApiClient(baseUrl = API_BASE_URL, fetchImpl?: typeof fetch) {
  const client = createClient<paths>({
    // Absolute URL even for same-origin so Request() works outside a browser (tests, workers).
    baseUrl: baseUrl || globalThis.location.origin,
    credentials: 'include',
    headers: { Accept: 'application/json, application/problem+json' },
    // Bearer token + refresh-on-401; fetch is resolved per call so tests / MSW can swap it.
    fetch: createAuthFetch(() => fetchImpl ?? globalThis.fetch),
  });
  client.use(throwOnError);
  return client;
}

export type ApiClient = ReturnType<typeof createApiClient>;

export const api = createApiClient();
