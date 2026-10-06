/** "mm:ss" / "h:mm:ss" / plain seconds → ms (null when unparseable). */
export function parseTimecode(text: string): number | null {
  const parts = text.trim().split(':');
  if (parts.length === 0 || parts.length > 3 || parts.some((p) => !/^\d+(\.\d+)?$/.test(p))) {
    return null;
  }
  const secs = parts.reduce((acc, p) => acc * 60 + Number(p), 0);
  return Math.round(secs * 1000);
}

const DAY_MS = 86_400_000;

/** Whole days left (0 = expires today); null when not applicable. */
export function daysLeft(expiresAt: string | null | undefined, now = Date.now()): number | null {
  if (!expiresAt) return null;
  return Math.max(0, Math.floor((new Date(expiresAt).getTime() - now) / DAY_MS));
}
