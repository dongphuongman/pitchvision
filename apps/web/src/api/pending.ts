import { API_BASE_URL } from './client';
import { ApiError } from './errors';
import { createAuthFetch } from './http';

/**
 * Transport for endpoints the web app needs before they appear in apps/api/openapi.json
 * (jobs, rename, delete, manifest — built concurrently by the backend; see `jobs.ts`).
 * Same auth + problem+json behaviour as the generated client. When an endpoint lands in the
 * OpenAPI spec, move its caller to `api.GET/POST/…` and delete it from here.
 */
const authFetch = createAuthFetch(() => globalThis.fetch);

export interface JsonRequestOptions {
  body?: unknown;
  signal?: AbortSignal;
  headers?: Record<string, string>;
}

export function apiUrl(path: string): string {
  return `${API_BASE_URL || globalThis.location.origin}${path}`;
}

export async function requestJson<T>(
  method: 'GET' | 'POST' | 'PATCH' | 'DELETE',
  path: string,
  opts: JsonRequestOptions = {},
): Promise<T> {
  const headers: Record<string, string> = {
    Accept: 'application/json, application/problem+json',
    ...opts.headers,
  };
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json';
  const request = new Request(apiUrl(path), {
    method,
    credentials: 'include',
    headers,
    body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
    signal: opts.signal,
  });
  let response: Response;
  try {
    response = await authFetch(request);
  } catch (e) {
    if (opts.signal?.aborted) throw e;
    throw ApiError.network(e);
  }
  if (!response.ok) throw await ApiError.fromResponse(response);
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

/** Idempotency-Key for POSTs that start work (AD §10.1). */
export function idempotencyKey(): string {
  return globalThis.crypto.randomUUID();
}
