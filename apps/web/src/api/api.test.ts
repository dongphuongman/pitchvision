import { createApiClient } from './client';
import { ApiError, isApiError, parseProblem } from './errors';
import { shouldRetry } from './queryClient';

function jsonResponse(body: unknown, status: number, contentType = 'application/json') {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': contentType } });
}

describe('parseProblem', () => {
  it('keeps RFC 9457 members and the PitchVision code', () => {
    const p = parseProblem(
      {
        type: 'https://docs.pitchvision.ai/errors/QUOTA_EXCEEDED',
        title: 'Quota exceeded',
        status: 402,
        code: 'QUOTA_EXCEEDED',
        detail: 'Monthly minutes used up',
        instance: '/api/v1/jobs',
        limit_min: 120,
      },
      402,
    );
    expect(p).toMatchObject({
      type: 'https://docs.pitchvision.ai/errors/QUOTA_EXCEEDED',
      title: 'Quota exceeded',
      status: 402,
      code: 'QUOTA_EXCEEDED',
      detail: 'Monthly minutes used up',
      instance: '/api/v1/jobs',
      limit_min: 120,
    });
  });

  it('derives a code from the HTTP status for non-problem bodies', () => {
    expect(parseProblem('<html>Bad gateway</html>', 502).code).toBe('INTERNAL');
    expect(parseProblem(null, 404).code).toBe('NOT_FOUND');
    expect(parseProblem({ code: 'SOMETHING_NEW' }, 418).code).toBe('UNKNOWN');
    expect(parseProblem(null, 0).code).toBe('NETWORK_ERROR');
  });
});

describe('API client', () => {
  it('returns typed data on success', async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(jsonResponse({ version: '0.1.0', schema_version: 1 }, 200)),
    );
    const client = createApiClient('http://api.test', fetchMock);
    const { data } = await client.GET('/api/v1/meta');
    expect(data).toEqual({ version: '0.1.0', schema_version: 1 });
    const req = (fetchMock.mock.calls[0] as unknown as [Request])[0];
    expect(req.url).toBe('http://api.test/api/v1/meta');
  });

  it('throws ApiError parsed from problem+json', async () => {
    const client = createApiClient(
      'http://api.test',
      vi.fn(() =>
        Promise.resolve(
          jsonResponse(
            { type: 'about:blank', title: 'Not found', status: 404, code: 'NOT_FOUND' },
            404,
            'application/problem+json',
          ),
        ),
      ),
    );
    const err: unknown = await client.GET('/api/v1/meta').catch((e: unknown) => e);
    expect(isApiError(err)).toBe(true);
    const apiErr = err as ApiError;
    expect(apiErr.status).toBe(404);
    expect(apiErr.code).toBe('NOT_FOUND');
    expect(apiErr.title).toBe('Not found');
    expect(apiErr.retryable).toBe(false);
  });

  it('wraps network failures as NETWORK_ERROR', async () => {
    const client = createApiClient(
      'http://api.test',
      vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))),
    );
    const err: unknown = await client.GET('/healthz').catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).code).toBe('NETWORK_ERROR');
    expect((err as ApiError).retryable).toBe(true);
  });
});

describe('query retry policy', () => {
  it('retries transient errors but not 4xx business errors', () => {
    const e503 = new ApiError(parseProblem(null, 503));
    const e422 = new ApiError(parseProblem({ code: 'VALIDATION_FAILED' }, 422));
    expect(shouldRetry(0, e503)).toBe(true);
    expect(shouldRetry(2, e503)).toBe(false);
    expect(shouldRetry(0, e422)).toBe(false);
  });
});
