import { useTranslation } from 'react-i18next';
import { usePreferences, type UnitSystem } from '@/lib/preferences';

/**
 * Locale-aware formatting. Everything goes through Intl with the active i18n language;
 * components never build number/date strings by hand.
 */

const M_PER_YD = 0.9144;

export function formatNumber(locale: string, value: number, opts?: Intl.NumberFormatOptions) {
  return new Intl.NumberFormat(locale, opts).format(value);
}

export function formatPercent(locale: string, pct: number, fractionDigits = 0) {
  return new Intl.NumberFormat(locale, {
    style: 'percent',
    maximumFractionDigits: fractionDigits,
  }).format(pct / 100);
}

export function formatDate(
  locale: string,
  date: Date | string | number,
  opts: Intl.DateTimeFormatOptions = { dateStyle: 'medium' },
) {
  return new Intl.DateTimeFormat(locale, opts).format(new Date(date));
}

export function formatRelative(locale: string, date: Date | string | number, now = Date.now()) {
  const diffS = Math.round((new Date(date).getTime() - now) / 1000);
  const abs = Math.abs(diffS);
  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: 'auto' });
  if (abs < 60) return rtf.format(diffS, 'second');
  if (abs < 3600) return rtf.format(Math.round(diffS / 60), 'minute');
  if (abs < 86_400) return rtf.format(Math.round(diffS / 3600), 'hour');
  return rtf.format(Math.round(diffS / 86_400), 'day');
}

/** Distance stored in metres (canonical); shown in metres by default, yards on request. */
export function formatDistance(locale: string, metres: number, units: UnitSystem = 'metric') {
  const value = units === 'imperial' ? metres / M_PER_YD : metres;
  return new Intl.NumberFormat(locale, {
    style: 'unit',
    unit: units === 'imperial' ? 'yard' : 'meter',
    unitDisplay: 'short',
    maximumFractionDigits: value < 10 ? 1 : 0,
  }).format(value);
}

/** Match clock / media timecode, e.g. 1:02:07 or 42:17. Digits are locale-independent by design. */
export function formatTimecode(ms: number) {
  const total = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = h > 0 ? String(m).padStart(2, '0') : String(m);
  return `${h > 0 ? `${h}:` : ''}${mm}:${String(s).padStart(2, '0')}`;
}

/** Bound formatters for the current language + unit preference. */
export function useFormat() {
  const { i18n } = useTranslation();
  const units = usePreferences((s) => s.units);
  const locale = i18n.language;
  return {
    locale,
    units,
    number: (v: number, o?: Intl.NumberFormatOptions) => formatNumber(locale, v, o),
    percent: (v: number, d?: number) => formatPercent(locale, v, d),
    date: (v: Date | string | number, o?: Intl.DateTimeFormatOptions) => formatDate(locale, v, o),
    relative: (v: Date | string | number) => formatRelative(locale, v),
    distance: (m: number) => formatDistance(locale, m, units),
    timecode: formatTimecode,
  };
}
