import type { Region } from '@/api/identity';

/** EU / EEA / CH / UK country codes → the EU data region (GDPR adequacy). */
const EUROPE = new Set(
  (
    'AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE ' +
    'IS LI NO CH GB'
  ).split(' '),
);

/**
 * Suggested data region for onboarding (UIUX SCR-07 E05: "pre-suggested from the browser
 * location"). Uses the time zone first (closest to physical location without asking for
 * geolocation), then the language tag's region. `null` = no confident guess.
 */
export function suggestRegion(
  timeZone: string | undefined = safeTimeZone(),
  languages: readonly string[] = typeof navigator === 'undefined' ? [] : navigator.languages,
): Region | null {
  if (timeZone) {
    if (timeZone.startsWith('Europe/') || timeZone.startsWith('Africa/')) return 'eu';
    if (timeZone.startsWith('America/') || timeZone.startsWith('US/')) return 'us';
    if (timeZone.startsWith('Atlantic/Reykjavik') || timeZone.startsWith('Atlantic/Canary')) {
      return 'eu';
    }
  }
  for (const tag of languages) {
    const country = tag.split('-')[1]?.toUpperCase();
    if (!country) continue;
    if (EUROPE.has(country)) return 'eu';
    if (country === 'US' || country === 'CA' || country === 'MX') return 'us';
  }
  return null;
}

function safeTimeZone(): string | undefined {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone;
  } catch {
    return undefined;
  }
}
