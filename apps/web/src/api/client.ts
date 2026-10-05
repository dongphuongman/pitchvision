import createClient, { type Middleware } from 'openapi-fetch';
import { ApiError } from './errors';
import type { paths } from './generated/openapi';

/** `VITE_API_URL` without trailing slash; empty = same origin (dev proxy / same-host deploy). */
export const API_BASE_URL = (import.meta.env.VITE_API_URL ?? '').replace(/\/+$/, '');

/**
 * Every non-2xx response becomes a thrown ApiError (problem+json parsed), so callers and
 * TanStack Query only ever see `data` on success and a typed error on failure.
 */
const throwOnError: Middleware = {
  async onResponse({ response }) {
    if (!response.ok) throw await ApiError.fromResponse(response);
    return undefined;
  },
  onError({ error }) {
    if (error instanceof ApiError) return error;
    return ApiError.network(error);
  },
};

export function createApiClient(baseUrl = API_BASE_URL, fetchImpl?: typeof fetch) {
  const client = createClient<paths>({
    // Absolute URL even for same-origin so Request() works outside a browser (tests, workers).
    baseUrl: baseUrl || globalThis.location.origin,
    credentials: 'include',
    headers: { Accept: 'application/json, application/problem+json' },
    // Resolve fetch per call (not at module load) so tests / MSW can swap globalThis.fetch.
    fetch: (input: Request) => (fetchImpl ?? globalThis.fetch)(input),
  });
  client.use(throwOnError);
  return client;
}

export type ApiClient = ReturnType<typeof createApiClient>;

export const api = createApiClient();
