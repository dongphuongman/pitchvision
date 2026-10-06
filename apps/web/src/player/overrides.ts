import type { Manifest, TileFrame } from '@/api/types';
import { OBJ } from './tileCodec';

/**
 * SCR-14 E06 fixes (manifest.track_overrides): tiles keep the detected team / class per track;
 * the player draws the user's correction on top. `makeOverrider` returns a per-manifest function
 * that patches a frame's object rows (memoised per frame object, so a repaint costs nothing).
 */
export type Overrider = (frame: TileFrame | undefined) => TileFrame | undefined;

export function makeOverrider(overrides: Manifest['track_overrides']): Overrider {
  const fixes = new Map((overrides ?? []).map((o) => [o.track_id, o]));
  if (fixes.size === 0) return (frame) => frame;
  const cache = new WeakMap<TileFrame, TileFrame>();
  return (frame) => {
    const rows = frame?.objs;
    if (!frame || !rows?.length) return frame;
    const hit = cache.get(frame);
    if (hit) return hit;
    const objs = rows.map((o) => {
      const fix = fixes.get(o[OBJ.trackId]);
      if (!fix) return o;
      const copy = [...o] as typeof o;
      copy[OBJ.team] = fix.team;
      copy[OBJ.cls] = fix.cls;
      return copy;
    });
    const out = objs.some((o, i) => o !== rows[i]) ? { ...frame, objs } : frame;
    cache.set(frame, out);
    return out;
  };
}
