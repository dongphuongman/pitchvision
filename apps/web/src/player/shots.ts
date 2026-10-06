import type { Shot, ShotType } from '@/api/types';

/** Shot covering `ms` (shots are contiguous and sorted; half-open [t0, t1)). */
export function shotAt(shots: readonly Shot[], ms: number): Shot | undefined {
  let lo = 0;
  let hi = shots.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    const s = shots[mid];
    if (s && s.t1_ms <= ms) lo = mid + 1;
    else hi = mid;
  }
  const s = shots[lo];
  return s && s.t0_ms <= ms ? s : undefined;
}

export interface TimelineSegment {
  type: ShotType;
  t0: number;
  t1: number;
  /** Percent of the duration. */
  left: number;
  width: number;
}

/** Bar segments for the shot timeline; adjacent shots of the same type are merged. */
export function timelineSegments(shots: readonly Shot[], durationMs: number): TimelineSegment[] {
  const total = durationMs > 0 ? durationMs : (shots.at(-1)?.t1_ms ?? 0);
  if (total <= 0) return [];
  const out: TimelineSegment[] = [];
  for (const s of shots) {
    const t0 = Math.max(0, Math.min(total, s.t0_ms));
    const t1 = Math.max(t0, Math.min(total, s.t1_ms));
    if (t1 === t0) continue;
    const prev = out.at(-1);
    if (prev && prev.type === s.type && prev.t1 === t0) {
      prev.t1 = t1;
      prev.width = ((t1 - prev.t0) / total) * 100;
    } else {
      out.push({
        type: s.type,
        t0,
        t1,
        left: (t0 / total) * 100,
        width: ((t1 - t0) / total) * 100,
      });
    }
  }
  return out;
}

/**
 * "Skip to next live action": start of the next main_wide shot after the current non-live one.
 * `null` while already in a live (main_wide) shot or when no live shot follows.
 */
export function nextLiveActionMs(shots: readonly Shot[], ms: number): number | null {
  const current = shotAt(shots, ms);
  if (current?.type === 'main_wide') return null;
  for (const s of shots) {
    if (s.type === 'main_wide' && s.t0_ms > ms) return s.t0_ms;
  }
  return null;
}

/** Shot type of frame `f` from a tile's `[f0, f1_exclusive, type]` runs. */
export function shotTypeInRuns(
  runs: readonly (readonly [number, number, ShotType])[],
  f: number,
): ShotType | undefined {
  for (const [f0, f1, type] of runs) if (f >= f0 && f < f1) return type;
  return undefined;
}
