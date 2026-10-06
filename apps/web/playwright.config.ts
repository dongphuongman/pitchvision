import { defineConfig, devices } from '@playwright/test';

/**
 * M1-FE-16 end-to-end flows (UIUX F1 / F3 / F4 / F8) in Chromium against the dev server in
 * mock-API mode (MSW in the browser: identity, videos, jobs over a simulated WebSocket, results,
 * exports). The real stack needs a GPU box; these cover the whole web app and its contracts.
 *   pnpm e2e            (starts `vite` on PORT with VITE_MOCK_API=1)
 */
const PORT = Number(process.env.PV_E2E_PORT ?? 23174);

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['github'], ['list']] : 'list',
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: 'retain-on-failure',
    ...devices['Desktop Chrome'],
    // PV_E2E_SLOWMO=400 pnpm e2e --headed: watch the flows step by step
    launchOptions: { slowMo: Number(process.env.PV_E2E_SLOWMO ?? 0) },
  },
  webServer: {
    command: `pnpm exec vite --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    env: { VITE_MOCK_API: '1', VITE_MOCK_TICK_MS: '80', VITE_MOCK_EXPORT_TICKS: '2' },
  },
});
