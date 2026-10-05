import { create } from 'zustand';
import { isApiError } from '@/api/errors';
import { setAuthHooks } from '@/api/http';
import { identityApi, type Membership, type Role, type Session, type User } from '@/api/identity';

/**
 * Client auth state (docs/contracts/identity-api.md):
 * - the access token (JWT, 15 min) lives in memory only — never localStorage;
 * - the refresh token is the HttpOnly `pv_refresh` cookie, so a reload restores the session
 *   with one `POST /auth/refresh` (silent refresh on app load);
 * - refresh is single-flight (and serialised across tabs with Web Locks) because the server
 *   rotates the cookie on every use and treats reuse of a rotated token as theft;
 * - a timer refreshes ~60 s before expiry; the HTTP layer refreshes once on 401 and retries.
 */

export type AuthStatus = 'unknown' | 'authenticated' | 'anonymous';

export interface AuthState {
  status: AuthStatus;
  accessToken: string | null;
  /** Epoch ms when the access token expires. */
  expiresAt: number | null;
  user: User | null;
  memberships: Membership[];
  activeOrgId: string | null;
}

const INITIAL: AuthState = {
  status: 'unknown',
  accessToken: null,
  expiresAt: null,
  user: null,
  memberships: [],
  activeOrgId: null,
};

export const useAuthStore = create<AuthState>()(() => ({ ...INITIAL }));

/** Refresh this long before the access token expires. */
export const REFRESH_LEAD_MS = 60_000;
const MIN_REFRESH_DELAY_MS = 5_000;

let refreshTimer: ReturnType<typeof setTimeout> | undefined;
let inflight: Promise<Session | null> | null = null;
let bootstrap: Promise<void> | null = null;

function scheduleRefresh(expiresInSec: number): void {
  clearTimeout(refreshTimer);
  const delay = Math.max(expiresInSec * 1000 - REFRESH_LEAD_MS, MIN_REFRESH_DELAY_MS);
  refreshTimer = setTimeout(() => void refreshSession(), delay);
}

/** Store a Session returned by login / register / refresh / verify / accept invite. */
export function applySession(session: Session): void {
  useAuthStore.setState({
    status: 'authenticated',
    accessToken: session.access_token,
    expiresAt: Date.now() + session.expires_in * 1000,
    user: session.user,
    memberships: session.memberships,
    activeOrgId: session.active_org_id,
  });
  scheduleRefresh(session.expires_in);
}

/** Forget the session locally (the server side is handled by `signOut`). */
export function clearSession(): void {
  clearTimeout(refreshTimer);
  refreshTimer = undefined;
  useAuthStore.setState({ ...INITIAL, status: 'anonymous' });
}

/** Test helper: back to the pristine "not bootstrapped yet" state. */
export function resetAuthForTests(): void {
  clearTimeout(refreshTimer);
  refreshTimer = undefined;
  inflight = null;
  bootstrap = null;
  useAuthStore.setState({ ...INITIAL });
}

async function withRefreshLock<T>(fn: () => Promise<T>): Promise<T> {
  // Serialise refreshes across tabs: two tabs presenting the same rotated cookie would trip
  // the server's reuse detection and revoke the whole token family.
  const locks = (globalThis.navigator as Navigator | undefined)?.locks;
  if (locks && typeof locks.request === 'function') {
    return locks.request('pv-auth-refresh', fn) as Promise<T>;
  }
  return fn();
}

async function doRefresh(orgId?: string): Promise<Session | null> {
  try {
    const session = await withRefreshLock(() => identityApi.refresh(orgId));
    applySession(session);
    return session;
  } catch (e) {
    // 401/403: the cookie is missing, expired or revoked → signed out. A network blip while
    // signed in keeps the current state (the next 401 retries); on app load it means anonymous.
    // An org switch refused with 403 (not a member) does not consume the cookie: the current
    // session stays valid.
    const signedIn = useAuthStore.getState().status === 'authenticated';
    const transient = isApiError(e) && e.status === 0 && signedIn;
    const switchRefused = Boolean(orgId) && isApiError(e) && e.status === 403 && signedIn;
    if (!transient && !switchRefused) clearSession();
    return null;
  }
}

/**
 * Single-flight `POST /auth/refresh`. Concurrent callers share one request; an org switch
 * (`orgId`) waits for any plain refresh in flight and then runs its own.
 */
export function refreshSession(opts: { orgId?: string } = {}): Promise<Session | null> {
  if (!opts.orgId && inflight) return inflight;
  const run = (inflight ?? Promise.resolve(null)).then(() => doRefresh(opts.orgId));
  const tracked = run.finally(() => {
    if (inflight === tracked) inflight = null;
  });
  inflight = tracked;
  return tracked;
}

/** Silent refresh on app load (once); later calls resolve immediately. */
export function ensureSession(): Promise<void> {
  if (useAuthStore.getState().status !== 'unknown') return Promise.resolve();
  bootstrap ??= refreshSession().then(() => undefined);
  return bootstrap;
}

/** Switch the active org: new token (org / role / region claims) via refresh `{org_id}`. */
export async function switchOrganization(orgId: string): Promise<boolean> {
  const session = await refreshSession({ orgId });
  return session?.active_org_id === orgId;
}

/** Revoke the refresh family server-side (best effort) and clear local state. */
export async function signOut(): Promise<void> {
  try {
    await identityApi.logout();
  } catch {
    /* already signed out / offline: local state is cleared regardless */
  }
  clearSession();
}

setAuthHooks({
  getAccessToken: () => useAuthStore.getState().accessToken,
  refresh: async () => (await refreshSession())?.access_token ?? null,
});

/* ---------- selectors ---------- */

export function selectActiveMembership(s: AuthState): Membership | null {
  return s.memberships.find((m) => m.org_id === s.activeOrgId) ?? null;
}

export function useActiveMembership(): Membership | null {
  return useAuthStore(selectActiveMembership);
}

export function useRole(): Role | null {
  return useAuthStore((s) => selectActiveMembership(s)?.role ?? null);
}

export function useUser(): User | null {
  return useAuthStore((s) => s.user);
}
