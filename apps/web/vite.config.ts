/// <reference types="vitest/config" />
import { readFileSync } from 'node:fs';
import { fileURLToPath, URL } from 'node:url';
import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { msw } from 'msw/vite';
import { defineConfig } from 'vite';

const pkg = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf8')) as {
  version: string;
};

export default defineConfig(({ command }) => ({
  // msw: serves mockServiceWorker.js for `VITE_MOCK_API=1 pnpm dev`; never part of a build.
  plugins: [react(), tailwindcss(), ...(command === 'serve' ? [msw({ mode: 'worker-only' })] : [])],
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
  },
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: { port: 5173 },
  build: {
    target: 'es2022',
    sourcemap: true,
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'], // e2e/*.spec.ts belong to Playwright (pnpm e2e)
    css: false,
    restoreMocks: true,
    // Full-app flows (router + lazy chunks + MSW + user-event typing) run longer than 5 s
    // when the whole suite shares the CPU.
    testTimeout: 15_000,
  },
}));
