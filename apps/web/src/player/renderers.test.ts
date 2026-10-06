import type { TileFrame } from '@/api/types';
import { mockShots, mockTileFrame } from '@/mocks/player';
import {
  BALL_COLOR,
  computeOverlay,
  fitContain,
  REFEREE_COLOR,
  teamColorMap,
  UNKNOWN_COLOR,
} from './overlay';
import { computeMapScene, mapTransform, pitchTemplate } from './tacticalMap';

const teams = teamColorMap([
  { cluster: 0, name: 'Home', color: '#e5484d' },
  { cluster: 1, name: 'Away', color: '#3e8bff' },
]);

const frame: TileFrame = {
  f: 1240,
  hq: 0.91,
  objs: [
    [7, 0, 0, 100, 200, 140, 300, 0.834, 34.2, 41.7],
    [19, 1, 1, 600, 220, 640, 320, 0.88, 3.1, 30.0],
    [30, 2, -1, 300, 210, 330, 290, 0.76, 50.0, 28.4],
    [99, 3, -1, 500, 400, 510, 410, 0.6, 49.8, 28.4],
    [55, 0, -1, 700, 200, 720, 260, 0.41, null, null],
  ],
};

describe('fitContain', () => {
  it('letterboxes and pillarboxes', () => {
    expect(fitContain(640, 360, 1280, 720)).toEqual({
      scale: 0.5,
      offsetX: 0,
      offsetY: 0,
      width: 640,
      height: 360,
    });
    expect(fitContain(640, 480, 1280, 720)).toMatchObject({ scale: 0.5, offsetY: 60 });
    expect(fitContain(800, 360, 1280, 720)).toMatchObject({ scale: 0.5, offsetX: 80 });
    expect(fitContain(0, 0, 1280, 720).scale).toBe(0);
  });
});

describe('computeOverlay', () => {
  const rect = fitContain(640, 360, 1280, 720); // displayWidth / 1280 = 0.5

  it('scales boxes by displayWidth / 1280 and colours them by team', () => {
    const cmds = computeOverlay(frame, 'main_wide', { rect, teamColors: teams });
    const boxes = cmds.filter((c) => c.kind === 'box');
    expect(boxes[0]).toEqual({
      kind: 'box',
      x: 50,
      y: 100,
      w: 20,
      h: 50,
      color: '#e5484d',
      dashed: false,
    });
    expect(boxes[1]).toMatchObject({ color: '#3e8bff' });
    expect(boxes[2]).toMatchObject({ color: REFEREE_COLOR, dashed: true });
    expect(boxes[3]).toMatchObject({ color: UNKNOWN_COLOR });
    expect(boxes).toHaveLength(4); // the ball is not a box
  });

  it('labels track id + conf (GK prefixed) after all boxes', () => {
    const cmds = computeOverlay(frame, 'main_wide', { rect, teamColors: teams });
    const firstLabel = cmds.findIndex((c) => c.kind === 'label');
    expect(cmds.slice(firstLabel).every((c) => c.kind === 'label')).toBe(true);
    const texts = cmds.flatMap((c) => (c.kind === 'label' ? [c.text] : []));
    expect(texts).toEqual(['#7 0.83', 'GK #19 0.88', '#30 0.76', '#55 0.41']);
    const noConf = computeOverlay(frame, 'main_wide', {
      rect,
      teamColors: teams,
      layers: { boxes: true, labels: true, conf: false, ball: true },
    });
    expect(noConf.find((c) => c.kind === 'label')).toMatchObject({ text: '#7' });
  });

  it('draws the ball as a distinct circle', () => {
    const ball = computeOverlay(frame, 'main_wide', { rect, teamColors: teams }).find(
      (c) => c.kind === 'ball',
    );
    expect(ball).toEqual({ kind: 'ball', x: 252.5, y: 202.5, r: 4.5 });
    expect(BALL_COLOR).toBe('#ffffff');
  });

  it('applies the letterbox offset', () => {
    const boxed = fitContain(640, 480, 1280, 720);
    const box = computeOverlay(frame, 'main_wide', { rect: boxed, teamColors: teams })[0];
    expect(box).toMatchObject({ x: 50, y: 160 });
  });

  it('draws nothing outside main_wide shots or without data', () => {
    expect(computeOverlay(frame, 'replay', { rect, teamColors: teams })).toEqual([]);
    expect(computeOverlay(frame, 'close_up', { rect, teamColors: teams })).toEqual([]);
    expect(computeOverlay(frame, undefined, { rect, teamColors: teams })).toEqual([]);
    expect(computeOverlay(undefined, 'main_wide', { rect, teamColors: teams })).toEqual([]);
  });
});

describe('computeMapScene', () => {
  it('plots players, referee and ball in metres; skips objects without pitch coords', () => {
    const scene = computeMapScene(frame, 'main_wide', { teamColors: teams });
    expect(scene.state).toBe('ok');
    expect(scene.dots.map((d) => [d.trackId, d.kind, d.x, d.y, d.color])).toEqual([
      [7, 'player', 34.2, 41.7, '#e5484d'],
      [19, 'goalkeeper', 3.1, 30, '#3e8bff'],
      [30, 'referee', 50, 28.4, REFEREE_COLOR],
      [99, 'ball', 49.8, 28.4, BALL_COLOR],
    ]);
  });

  it('greys out with "Low confidence" when h_quality < 0.5 or missing', () => {
    expect(computeMapScene({ ...frame, hq: 0.42 }, 'main_wide', { teamColors: teams })).toEqual({
      state: 'low_confidence',
      dots: [],
      hq: 0.42,
    });
    expect(computeMapScene({ ...frame, hq: null }, 'main_wide', { teamColors: teams }).state).toBe(
      'low_confidence',
    );
    expect(computeMapScene({ ...frame, hq: 0.5 }, 'main_wide', { teamColors: teams }).state).toBe(
      'ok',
    );
    expect(
      computeMapScene({ ...frame, hq: 0.6 }, 'main_wide', { teamColors: teams, hqThreshold: 0.7 })
        .state,
    ).toBe('low_confidence');
  });

  it('is "not available" outside the wide shot and "no data" before tiles arrive', () => {
    expect(computeMapScene(frame, 'replay', { teamColors: teams })).toMatchObject({
      state: 'not_available',
      shot: 'replay',
      dots: [],
    });
    expect(computeMapScene(undefined, undefined, { teamColors: teams }).state).toBe('no_data');
    expect(computeMapScene(undefined, 'main_wide', { teamColors: teams }).state).toBe('no_data');
  });

  it('fits the 105×68 pitch into the canvas', () => {
    const t = mapTransform(222, 148, 3);
    expect(t.scale).toBeCloseTo(2);
    expect(t.ox).toBeCloseTo(6);
    expect(t.oy).toBeCloseTo(6);
  });

  it('has the standard template lines', () => {
    const shapes = pitchTemplate();
    expect(shapes.filter((s) => s.kind === 'rect')).toHaveLength(5); // outline + 2 PA + 2 GA
    expect(shapes.filter((s) => s.kind === 'spot')).toHaveLength(3);
    expect(shapes.filter((s) => s.kind === 'arc')).toHaveLength(2);
    expect(shapes).toContainEqual({ kind: 'circle', x: 52.5, y: 34, r: 9.15 });
  });
});

describe('mock generator', () => {
  it('produces contiguous shots and tracking only in wide shots', () => {
    const shots = mockShots(150_000);
    for (let i = 1; i < shots.length; i += 1) {
      expect(shots[i]?.t0_ms).toBe(shots[i - 1]?.t1_ms);
    }
    expect(shots.at(-1)?.t1_ms).toBe(150_000);
    expect(mockTileFrame(25 * 10).objs?.length).toBeGreaterThan(5); // 10 s: wide
    expect(mockTileFrame(25 * 36)).toEqual({ f: 900, hq: null, objs: [] }); // 36 s: replay
    expect(mockTileFrame(25 * 22).hq).toBeLessThan(0.5); // 22 s: low homography
  });
});
