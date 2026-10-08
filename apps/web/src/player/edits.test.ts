import { describe, expect, it } from 'vitest';
import type { Tile } from '@/api/types';
import { applyCorrectionsToTile } from './edits';

function obj(
  track: number,
  team = 0,
  cls = 0,
): [number, number, number, number, number, number, number, number, null, null] {
  return [track, cls, team, 10, 20, 30, 80, 0.9, null, null];
}

function tile(frames: { f: number; tracks: number[] }[]): Tile {
  return {
    fps: 25,
    t0_ms: 0,
    t1_ms: 10_000,
    tile_no: 0,
    frames: frames.map((fr) => ({ f: fr.f, objs: fr.tracks.map((t) => obj(t)) })),
  };
}

describe('applyCorrectionsToTile (SCR-22 optimistic)', () => {
  it('returns the same reference when there is nothing to apply', () => {
    const t = tile([{ f: 0, tracks: [1] }]);
    expect(applyCorrectionsToTile(t, [])).toBe(t);
    expect(applyCorrectionsToTile(t, [{ type: 'merge', payload: {}, undone: true }])).toBe(t);
  });

  it('merge renames from → into (into wins on shared frames)', () => {
    const t = tile([
      { f: 0, tracks: [1, 2] },
      { f: 25, tracks: [1] },
    ]);
    const out = applyCorrectionsToTile(t, [
      { type: 'merge', payload: { from: 1, into: 2 }, undone: false },
    ]);
    expect(out.frames[0]?.objs?.map((o) => o[0]).sort()).toEqual([2]);
    expect(out.frames[1]?.objs?.map((o) => o[0])).toEqual([2]);
  });

  it('merge drops the `from` row when both tracks share a frame (into wins)', () => {
    const t = tile([{ f: 0, tracks: [1, 2] }]);
    const out = applyCorrectionsToTile(t, [
      { type: 'merge', payload: { from: 1, into: 2 }, undone: false },
    ]);
    expect(out.frames[0]?.objs?.map((o) => o[0])).toEqual([2]);
  });

  it('split renames frames at/after at_ms', () => {
    const t = tile([
      { f: 0, tracks: [7] }, // 0 ms
      { f: 25, tracks: [7] }, // 1000 ms
      { f: 50, tracks: [7] }, // 2000 ms
    ]);
    const out = applyCorrectionsToTile(t, [
      { type: 'split', payload: { track: 7, at_ms: 1000, new_track: 99 }, undone: false },
    ]);
    expect(out.frames[0]?.objs?.map((o) => o[0])).toEqual([7]);
    expect(out.frames[1]?.objs?.map((o) => o[0])).toEqual([99]);
    expect(out.frames[2]?.objs?.map((o) => o[0])).toEqual([99]);
  });

  it('remove drops a range, or the whole track when open', () => {
    const t = tile([
      { f: 0, tracks: [5] }, // 0 ms
      { f: 25, tracks: [5] }, // 1000 ms
    ]);
    const ranged = applyCorrectionsToTile(t, [
      { type: 'remove', payload: { track: 5, from_ms: 1000, to_ms: 2000 }, undone: false },
    ]);
    expect(ranged.frames[0]?.objs?.length).toBe(1);
    expect(ranged.frames[1]?.objs?.length).toBe(0);

    const whole = applyCorrectionsToTile(t, [
      { type: 'remove', payload: { track: 5 }, undone: false },
    ]);
    expect(whole.frames[0]?.objs?.length).toBe(0);
    expect(whole.frames[1]?.objs?.length).toBe(0);
  });

  it('applies a burst in order (split then merge)', () => {
    const t = tile([{ f: 25, tracks: [7, 8] }]);
    const out = applyCorrectionsToTile(t, [
      { type: 'split', payload: { track: 7, at_ms: 500, new_track: 99 }, undone: false },
      { type: 'merge', payload: { from: 99, into: 8 }, undone: false },
    ]);
    // 1000 ms ≥ 500 → 7 becomes 99, then 99 merges into 8 (clash → into wins).
    expect(out.frames[0]?.objs?.map((o) => o[0])).toEqual([8]);
  });
});
