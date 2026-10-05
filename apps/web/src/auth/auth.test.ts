import { http, HttpResponse } from 'msw';
import { api } from '@/api/client';
import { isApiError } from '@/api/errors';
import { problem } from '@/mocks/identity';
import { setupIdentityMock } from '@/test/msw';
import { testSession } from '@/test/renderApp';
import { nextAfterAuth, safeReturnTo } from './redirects';
import { can } from './roles';
import { applySession, refreshSession, switchOrganization, useAuthStore } from './session';

describe('safeReturnTo (open-redirect guard)', () => {
  it.each([
    ['/library', '/library'],
    ['/videos/1/overview?t=12#x', '/videos/1/overview?t=12#x'],
    ['/invite/abc', '/invite/abc'],
  ])('keeps same-origin path %s', (input, expected) => {
    expect(safeReturnTo(input)).toBe(expected);
  });

  it.each([
    'https://evil.com/library',
    '//evil.com',
    '///evil.com',
    '/\\evil.com',
    '\\\\evil.com',
    'javascript:alert(1)',
    ' //evil.com',
    '/\t/evil.com',
    'library',
    '',
    '/signin?return_to=/library',
    '/onboarding/organization',
  ])('rejects %j', (input) => {
    expect(safeReturnTo(input)).toBeUndefined();
  });

  it('rejects non-strings', () => {
    expect(safeReturnTo(undefined)).toBeUndefined();
    expect(safeReturnTo(42)).toBeUndefined();
    expect(safeReturnTo(['/library'])).toBeUndefined();
  });
});

describe('nextAfterAuth (SCR-02 E05 rules)', () => {
  it('unverified → verify email, keeping return_to', () => {
    expect(nextAfterAuth(testSession({ emailVerified: false }), '/exports')).toBe(
      '/verify-email?return_to=%2Fexports',
    );
  });
  it('no organization → onboarding', () => {
    expect(nextAfterAuth(testSession({ memberships: [] }))).toBe('/onboarding/organization');
  });
  it('return_to when safe, else the library', () => {
    expect(nextAfterAuth(testSession(), '/exports')).toBe('/exports');
    expect(nextAfterAuth(testSession(), 'https://evil.com')).toBe('/library');
    expect(nextAfterAuth(testSession())).toBe('/library');
  });
});

describe('can(role, min)', () => {
  it('orders viewer < editor < admin < owner', () => {
    expect(can('viewer', 'editor')).toBe(false);
    expect(can('editor', 'editor')).toBe(true);
    expect(can('admin', 'editor')).toBe(true);
    expect(can('admin', 'owner')).toBe(false);
    expect(can('owner', 'admin')).toBe(true);
    expect(can(null, 'viewer')).toBe(false);
  });
});

describe('refresh on 401', () => {
  const mock = setupIdentityMock();

  function stubApi(counter: { refresh: number; meta: string[] }, refreshResult: 'ok' | 'fail') {
    mock.server.use(
      http.post('*/api/v1/auth/refresh', async () => {
        counter.refresh += 1;
        await new Promise((r) => setTimeout(r, 20)); // keep it in flight while others 401
        if (refreshResult === 'fail') return problem(401, 'UNAUTHENTICATED');
        return HttpResponse.json({ ...testSession(), access_token: 'fresh' });
      }),
      http.get('*/api/v1/meta', ({ request }) => {
        const auth = request.headers.get('Authorization') ?? '';
        counter.meta.push(auth);
        return auth === 'Bearer fresh'
          ? HttpResponse.json({ version: '1.0.0', schema_version: 1 })
          : problem(401, 'UNAUTHENTICATED');
      }),
    );
  }

  it('refreshes once for concurrent 401s (single flight) and retries each request once', async () => {
    const counter = { refresh: 0, meta: [] as string[] };
    stubApi(counter, 'ok');
    applySession({ ...testSession(), access_token: 'stale' });

    const results = await Promise.all([
      api.GET('/api/v1/meta'),
      api.GET('/api/v1/meta'),
      api.GET('/api/v1/meta'),
    ]);

    expect(results.map((r) => r.data?.version)).toEqual(['1.0.0', '1.0.0', '1.0.0']);
    expect(counter.refresh).toBe(1);
    expect(counter.meta.filter((a) => a === 'Bearer stale')).toHaveLength(3);
    expect(counter.meta.filter((a) => a === 'Bearer fresh')).toHaveLength(3);
    expect(useAuthStore.getState().accessToken).toBe('fresh');
  });

  it('signs out and surfaces the 401 when the refresh fails', async () => {
    const counter = { refresh: 0, meta: [] as string[] };
    stubApi(counter, 'fail');
    applySession({ ...testSession(), access_token: 'stale' });

    const error = await api.GET('/api/v1/meta').catch((e: unknown) => e);
    expect(isApiError(error) && error.code).toBe('UNAUTHENTICATED');
    expect(counter.refresh).toBe(1);
    expect(counter.meta).toEqual(['Bearer stale']); // no retry without a new token
    expect(useAuthStore.getState().status).toBe('anonymous');
  });

  it('a refused org switch (403, not a member) keeps the current session', async () => {
    mock.server.use(http.post('*/api/v1/auth/refresh', () => problem(403, 'FORBIDDEN')));
    applySession(testSession());
    expect(await switchOrganization('org-x')).toBe(false);
    expect(useAuthStore.getState().status).toBe('authenticated');
    expect(useAuthStore.getState().activeOrgId).toBe('org-1');
  });

  it('shares one in-flight refresh between callers', async () => {
    const counter = { refresh: 0, meta: [] as string[] };
    stubApi(counter, 'ok');
    const [a, b] = await Promise.all([refreshSession(), refreshSession()]);
    expect(counter.refresh).toBe(1);
    expect(a?.access_token).toBe('fresh');
    expect(b).toBe(a);
  });
});
