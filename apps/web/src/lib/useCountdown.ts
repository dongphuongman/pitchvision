import { useCallback, useEffect, useState } from 'react';

/**
 * Seconds-remaining countdown for cooldowns ("Resend in 0:52", sign-in lockout).
 * `start(seconds)` (re)starts it; `remaining` is 0 when idle.
 */
export function useCountdown(): { remaining: number; start: (seconds: number) => void } {
  const [endsAt, setEndsAt] = useState<number | null>(null);
  const [remaining, setRemaining] = useState(0);

  useEffect(() => {
    if (endsAt === null) return;
    const tick = () => {
      const left = Math.max(0, Math.ceil((endsAt - Date.now()) / 1000));
      setRemaining(left);
      if (left === 0) setEndsAt(null);
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [endsAt]);

  const start = useCallback((seconds: number) => {
    setEndsAt(Date.now() + seconds * 1000);
  }, []);

  return { remaining, start };
}

/** 52 → "0:52", 905 → "15:05". */
export function formatCountdown(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}
