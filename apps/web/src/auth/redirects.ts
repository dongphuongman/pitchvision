import type { Membership, User } from '@/api/identity';

/** Auth-flow screens never make sense as a post-login destination (and could loop). */
const AUTH_FLOW_PATHS = [
  '/signin',
  '/signup',
  '/forgot-password',
  '/reset-password',
  '/verify-email',
  '/onboarding',
  '/auth/callback',
];

const PROBE_ORIGIN = 'https://pv.invalid';

function hasControlChar(value: string): boolean {
  for (let i = 0; i < value.length; i++) {
    const c = value.charCodeAt(i);
    if (c <= 0x1f || c === 0x7f) return true;
  }
  return false;
}

/**
 * Open-redirect guard for `?return_to=`: only same-origin, relative paths survive
 * ("/videos/1/overview?t=3"). Rejects absolute and protocol-relative URLs ("//evil.com",
 * "/\\evil.com"), schemes ("javascript:"), control characters and auth-flow pages.
 */
export function safeReturnTo(raw: unknown): string | undefined {
  if (typeof raw !== 'string') return undefined;
  const value = raw.trim();
  if (!value.startsWith('/') || value.startsWith('//')) return undefined;
  // Browsers treat "\" like "/" ("/\evil.com" → //evil.com); control chars can hide either.
  if (value.includes('\\') || hasControlChar(value)) return undefined;
  let url: URL;
  try {
    url = new URL(value, PROBE_ORIGIN);
  } catch {
    return undefined;
  }
  if (url.origin !== PROBE_ORIGIN) return undefined;
  if (AUTH_FLOW_PATHS.some((p) => url.pathname === p || url.pathname.startsWith(`${p}/`))) {
    return undefined;
  }
  return `${url.pathname}${url.search}${url.hash}`;
}

function withReturnTo(path: string, returnTo: string | undefined): string {
  return returnTo ? `${path}?${new URLSearchParams({ return_to: returnTo }).toString()}` : path;
}

export interface SessionLike {
  user: Pick<User, 'email_verified'> | null;
  memberships: readonly Membership[];
}

/**
 * Where to go after sign-in / sign-up / verification (UIUX SCR-02 E05, F2):
 * unverified → SCR-06, no org → SCR-07, else `return_to` or the library.
 * `return_to` is carried through verification and onboarding.
 */
export function nextAfterAuth(session: SessionLike, rawReturnTo?: unknown): string {
  const returnTo = safeReturnTo(rawReturnTo);
  if (!session.user) return withReturnTo('/signin', returnTo);
  if (!session.user.email_verified) return withReturnTo('/verify-email', returnTo);
  if (session.memberships.length === 0) return withReturnTo('/onboarding/organization', returnTo);
  return returnTo ?? '/library';
}
