import type { TileFrame } from '@/api/types';
import { makeOverrider } from './overrides';

const frame = (): TileFrame => ({
  f: 0,
  hq: null,
  objs: [
    [7, 1, 0, 10, 10, 20, 40, 0.9, null, null],
    [8, 0, 1, 30, 10, 40, 40, 0.8, null, null],
  ],
});

describe('makeOverrider (SCR-14 E06)', () => {
  it('patches team / class of fixed tracks, leaves the rest, memoises per frame', () => {
    const fix = makeOverrider([{ track_id: 7, team: 1, cls: 1 }]);
    const f = frame();
    const out = fix(f);
    expect(out?.objs?.[0]?.slice(0, 3)).toEqual([7, 1, 1]);
    expect(out?.objs?.[1]).toBe(f.objs?.[1]);
    expect(f.objs?.[0]?.[2]).toBe(0); // the tile's frame is not mutated
    expect(fix(f)).toBe(out);
  });

  it('is the identity without overrides or matches', () => {
    const f = frame();
    expect(makeOverrider([])(f)).toBe(f);
    expect(makeOverrider(undefined)(f)).toBe(f);
    expect(makeOverrider([{ track_id: 99, team: -1, cls: 2 }])(f)).toBe(f);
    expect(makeOverrider([{ track_id: 7, team: 1, cls: 1 }])(undefined)).toBeUndefined();
  });
});
