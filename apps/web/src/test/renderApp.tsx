import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, RouterProvider } from '@tanstack/react-router';
import { render } from '@testing-library/react';
import type { Membership, Role, Session } from '@/api/identity';
import { applySession, clearSession } from '@/auth/session';
import { createAppRouter } from '@/router';

export interface TestSessionOptions {
  role?: Role;
  emailVerified?: boolean;
  memberships?: Membership[];
  activeOrgId?: string | null;
  name?: string;
  email?: string;
}

/** A Session as the identity service returns it (defaults: verified owner of one EU org). */
export function testSession(opts: TestSessionOptions = {}): Session {
  const memberships = opts.memberships ?? [
    { org_id: 'org-1', org_name: 'Riverside FC', role: opts.role ?? 'owner', region: 'eu' },
  ];
  return {
    access_token: 'test-token',
    token_type: 'bearer',
    expires_in: 900,
    user: {
      id: 'u-1',
      name: opts.name ?? 'Dong Man',
      email: opts.email ?? 'dm@riversidefc.com',
      email_verified: opts.emailVerified ?? true,
      created_at: '2026-10-01T00:00:00Z',
    },
    memberships,
    active_org_id:
      opts.activeOrgId !== undefined ? opts.activeOrgId : (memberships[0]?.org_id ?? null),
  };
}

/**
 * Renders the full app (router + shell) at `url` with in-memory history.
 * `auth`: a Session to start signed in (default: verified owner), `null` to start signed out,
 * or `'bootstrap'` to let the app run its real silent refresh (for MSW-backed tests).
 */
export function renderApp(url: string, opts: { auth?: Session | null | 'bootstrap' } = {}) {
  const auth = opts.auth === undefined ? testSession() : opts.auth;
  if (auth === null) clearSession();
  else if (auth !== 'bootstrap') applySession(auth);

  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { retry: false } },
  });
  const history = createMemoryHistory({ initialEntries: [url] });
  const router = createAppRouter({ queryClient, history });
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { ...utils, router, history, queryClient };
}
