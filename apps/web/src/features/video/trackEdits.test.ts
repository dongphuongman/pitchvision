import { describe, expect, it } from 'vitest';
import { pickMapTrack, pickVideoTrack } from './trackEdits';

describe('pickVideoTrack (SCR-22 hit-test)', () => {
  // [track_id, cls, team, x1, y1, x2, y2, conf, px, py]
  const objs: [number, number, number, number, number, number, number, number, null, null][] = [
    [1, 0, 0, 10, 20, 30, 80, 0.9, null, null],
    [2, 0, 1, 100, 100, 140, 180, 0.8, null, null],
    [9, 3, 0, 12, 22, 20, 30, 0.5, null, null], // ball inside track 1: ignored
  ];

  it('picks the box containing the point', () => {
    expect(pickVideoTrack(objs, 20, 50)).toBe(1);
    expect(pickVideoTrack(objs, 120, 150)).toBe(2);
  });

  it('returns null outside every box', () => {
    expect(pickVideoTrack(objs, 500, 500)).toBeNull();
    expect(pickVideoTrack(undefined, 20, 50)).toBeNull();
  });

  it('prefers the smallest box when they overlap', () => {
    const overlapping = [
      ...objs,
      [3, 0, 0, 0, 0, 200, 200, 0.7, null, null] as (typeof objs)[number],
    ];
    expect(pickVideoTrack(overlapping, 20, 50)).toBe(1);
  });
});

describe('pickMapTrack (SCR-22 hit-test)', () => {
  const dots = [
    { trackId: 1, x: 52.5, y: 34 },
    { trackId: 2, x: 70, y: 20 },
  ];

  it('picks the nearest dot within tolerance', () => {
    expect(pickMapTrack(dots, 52.8, 34.2)).toBe(1);
    expect(pickMapTrack(dots, 70.5, 20.5)).toBe(2);
  });

  it('returns null when nothing is close enough', () => {
    expect(pickMapTrack(dots, 10, 10)).toBeNull();
    expect(pickMapTrack([], 52.5, 34)).toBeNull();
  });
});
