/**
 * Browser errors to Sentry (AD §17), configured at runtime by `/config.js` (the web image renders it from env), so
 * one image serves staging and production. Nothing loads when no DSN is set (dev, tests, self-host).
 */
export interface RuntimeConfig {
  sentryDsn?: string;
  environment?: string;
  release?: string;
}

declare global {
  interface Window {
    __PV_CONFIG__?: RuntimeConfig;
  }
}

export function runtimeConfig(): RuntimeConfig {
  return globalThis.window.__PV_CONFIG__ ?? {};
}

export async function initMonitoring(config: RuntimeConfig = runtimeConfig()): Promise<boolean> {
  if (!config.sentryDsn) return false;
  const Sentry = await import('@sentry/react');
  Sentry.init({
    dsn: config.sentryDsn,
    environment: config.environment ?? 'production',
    release: config.release,
    sendDefaultPii: false,
    // no request / response bodies, no replays: tokens and match data stay out of Sentry
    integrations: [],
  });
  return true;
}
