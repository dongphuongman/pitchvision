import { http, HttpResponse } from 'msw';
import { setupWorker } from 'msw/browser';
import { identityHandlers, type MockDb } from './identity';
import { demoDb } from './seed';

/**
 * Dev-only in-browser API (`VITE_MOCK_API=1 pnpm dev`). The worker script is served by the
 * msw/vite plugin in dev only; nothing here reaches production builds (guarded in main.tsx).
 * The simulated refresh cookie is mirrored to sessionStorage so a reload keeps you signed in.
 */
const COOKIE_KEY = 'pv-mock-refresh';

function persistCookie(db: MockDb): void {
  let cookie: MockDb['cookie'] = null;
  try {
    const saved = sessionStorage.getItem(COOKIE_KEY);
    if (saved) cookie = JSON.parse(saved) as MockDb['cookie'];
  } catch {
    /* ignore */
  }
  Object.defineProperty(db, 'cookie', {
    get: () => cookie,
    set: (value: MockDb['cookie']) => {
      cookie = value;
      try {
        sessionStorage.setItem(COOKIE_KEY, JSON.stringify(value));
      } catch {
        /* ignore */
      }
    },
  });
}

export async function startMockApi(): Promise<void> {
  const db = demoDb();
  persistCookie(db);
  const worker = setupWorker(
    ...identityHandlers(db),
    http.get('*/api/v1/meta', () => HttpResponse.json({ version: 'mock', schema_version: 1 })),
  );
  await worker.start({ onUnhandledFrame: 'bypass' });
}
