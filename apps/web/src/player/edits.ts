import type { CorrectionOut } from '@/api/corrections';
import type { Tile } from '@/api/types';
import { msForFrame } from './frames';
import { OBJ } from './tileCodec';

/**
 * SCR-22 optimistic corrections (M2-FE-03): apply the live correction list on top of a
 * playback tile, in order, exactly like the server's `edit_tracks` (merge / split / remove).
 * Undone corrections are skipped by the caller (GET only returns live ones, but the
 * toast-optimistic caller may pass a mixed list).
 */
export type LiveCorrection = Pick<CorrectionOut, 'type' | 'payload' | 'undone'>;

function num(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

function nullableMs(v: unknown): number | null {
  return v === null || v === undefined ? null : num(v);
}

/** Merge target's team/class as seen in this tile (first `into` row wins, like the server). */
function mergeStyle(tile: Tile, into: number): { team: number; cls: number } | null {
  for (const fr of tile.frames) {
    for (const o of fr.objs ?? []) {
      if (o[OBJ.trackId] === into) return { team: o[OBJ.team], cls: o[OBJ.cls] };
    }
  }
  return null;
}

export function applyCorrectionsToTile(tile: Tile, corrections: readonly LiveCorrection[]): Tile {
  const live = corrections.filter((c) => !c.undone);
  if (live.length === 0) return tile;
  const fps = tile.fps || 25;

  // Pre-scan merge styles (team/class of the merge target as seen in this tile).
  const styles = new Map<string, { team: number; cls: number }>();
  for (const c of live) {
    if (c.type !== 'merge') continue;
    const from = num(c.payload['from']);
    const into = num(c.payload['into']);
    if (from === null || into === null || from === into) continue;
    const s = mergeStyle(tile, into);
    if (s) styles.set(`${String(from)}→${String(into)}`, s);
  }

  const frames = tile.frames.map((fr) => {
    const ms = msForFrame(fr.f, fps);
    let objs = fr.objs ?? [];
    let frameChanged: boolean = false;

    for (const c of live) {
      if (c.type === 'merge') {
        const from = num(c.payload['from']);
        const into = num(c.payload['into']);
        if (from === null || into === null || from === into) continue;
        if (!objs.some((o) => o[OBJ.trackId] === from)) continue;
        const hasInto = objs.some((o) => o[OBJ.trackId] === into);
        const style = styles.get(`${String(from)}→${String(into)}`);
        if (hasInto) {
          // Same frame has both: `into` wins (a track can't be in two places).
          objs = objs.filter((o) => o[OBJ.trackId] !== from);
        } else {
          objs = objs.map((o) => {
            if (o[OBJ.trackId] !== from) return o;
            const copy = [...o] as typeof o;
            copy[OBJ.trackId] = into;
            if (style) {
              copy[OBJ.team] = style.team;
              copy[OBJ.cls] = style.cls;
            }
            return copy;
          });
        }
        frameChanged = true;
      } else if (c.type === 'split') {
        const track = num(c.payload['track']);
        const atMs = num(c.payload['at_ms']);
        const next = num(c.payload['new_track']);
        if (track === null || atMs === null || next === null || next === track) continue;
        if (ms < atMs) continue;
        if (!objs.some((o) => o[OBJ.trackId] === track)) continue;
        objs = objs.map((o) => {
          if (o[OBJ.trackId] !== track) return o;
          const copy = [...o] as typeof o;
          copy[OBJ.trackId] = next;
          return copy;
        });
        frameChanged = true;
      } else if (c.type === 'remove') {
        const track = num(c.payload['track']);
        if (track === null) continue;
        const fromMs = nullableMs(c.payload['from_ms']);
        const toMs = nullableMs(c.payload['to_ms']);
        if (fromMs !== null && toMs !== null && toMs <= fromMs) continue;
        const inRange = (fromMs === null || ms >= fromMs) && (toMs === null || ms < toMs);
        if (!inRange) continue;
        if (!objs.some((o) => o[OBJ.trackId] === track)) continue;
        objs = objs.filter((o) => o[OBJ.trackId] !== track);
        frameChanged = true;
      }
    }

    if (!frameChanged) return fr;
    return { ...fr, objs };
  });

  // Unchanged frames keep their reference: a fully-untouched tile returns as-is.
  if (frames.every((fr, i) => fr === tile.frames[i])) return tile;
  return { ...tile, frames };
}

/** Track ids visible in a frame (players only by default; ball has track -1 or its own id). */
export function tracksInFrame(
  objs: readonly (readonly number[])[] | undefined,
  opts: { includeBall?: boolean } = {},
): number[] {
  if (!objs) return [];
  const out = new Set<number>();
  for (const o of objs) {
    const id = o[0];
    const cls = o[1];
    if (typeof id !== 'number') continue;
    if (!opts.includeBall && cls === 3) continue;
    out.add(id);
  }
  return [...out].sort((a, b) => a - b);
}
