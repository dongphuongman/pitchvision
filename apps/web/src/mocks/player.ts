import { http, HttpResponse } from 'msw';
import type { Manifest, Shot, ShotType, Tile, TileFrame } from '@/api/types';

/**
 * Synthetic playback data for any video: a deterministic manifest + tiles with 22 players, a
 * referee and the ball moving smoothly, seen through a fake broadcast camera that pans with the
 * ball. Shots repeat every minute (wide → replay → wide → close-up) and seconds 20–26 of every
 * minute have low homography quality, so every renderer state is visible in /__player.
 * Tiles are served gzipped (CompressionStream) exactly like `…/{tile_no}.json.gz` objects.
 */

export const MOCK_FPS = 25;
export const MOCK_WIDTH = 1280;
export const MOCK_HEIGHT = 720;
export const MOCK_TILE_MS = 10_000;
const FPT = (MOCK_TILE_MS * MOCK_FPS) / 1000;

/** Repeating 60 s pattern [start s, end s, type]. */
const CYCLE: [number, number, ShotType][] = [
  [0, 34, 'main_wide'],
  [34, 42, 'replay'],
  [42, 54, 'main_wide'],
  [54, 57, 'close_up'],
  [57, 60, 'main_wide'],
];

export function mockShots(durationMs: number): Shot[] {
  const shots: Shot[] = [];
  for (let base = 0; base < durationMs; base += 60_000) {
    for (const [a, b, type] of CYCLE) {
      const t0 = base + a * 1000;
      const t1 = Math.min(durationMs, base + b * 1000);
      if (t1 <= t0) continue;
      const prev = shots.at(-1);
      if (prev && prev.type === type && prev.t1_ms === t0) prev.t1_ms = t1;
      else shots.push({ t0_ms: t0, t1_ms: t1, type, conf: type === 'main_wide' ? 0.93 : 0.81 });
    }
  }
  return shots;
}

function shotTypeAtMs(ms: number): ShotType {
  const s = (ms % 60_000) / 1000;
  for (const [a, b, type] of CYCLE) if (s >= a && s < b) return type;
  return 'main_wide';
}

function hqAtMs(ms: number): number {
  const s = (ms % 60_000) / 1000;
  return s >= 20 && s < 26 ? 0.32 : 0.86 + 0.08 * Math.sin(ms / 3100);
}

export const MOCK_TEAMS = [
  { cluster: 0, name: 'Riverside FC', color: '#e5484d', confirmed: true },
  { cluster: 1, name: 'Harbor City', color: '#3e8bff', confirmed: true },
];

export function mockManifest(opts: {
  videoId: string;
  durationMs: number;
  tileBase?: string;
}): Manifest {
  const { videoId, durationMs } = opts;
  const shots = mockShots(durationMs);
  const wide = shots
    .filter((s) => s.type === 'main_wide')
    .reduce((a, s) => a + s.t1_ms - s.t0_ms, 0);
  const base = opts.tileBase ?? '/__mock/tiles';
  return {
    video_id: videoId,
    job_id: `job-${videoId}`,
    revision: 1,
    schema_version: 1,
    fps: MOCK_FPS,
    width: MOCK_WIDTH,
    height: MOCK_HEIGHT,
    duration_ms: durationMs,
    frame_count: Math.round((durationMs * MOCK_FPS) / 1000),
    tile_ms: MOCK_TILE_MS,
    tile_count: Math.max(1, Math.ceil(durationMs / MOCK_TILE_MS)),
    tile_url_template: `${base}/${encodeURIComponent(videoId)}/{tile_no}.json.gz?d=${durationMs}`,
    coverage: {
      main_wide_pct: durationMs ? Math.round((wide / durationMs) * 1000) / 10 : 0,
      homography_ok_pct: 90,
    },
    pitch: { status: 'ok', length_m: 105, width_m: 68 },
    shots,
    teams: MOCK_TEAMS,
  };
}

/* 4-4-2 vs 4-4-2, metres (x along the 105 m touchline). Home attacks right. */
const HOME: [number, number][] = [
  [6, 34],
  [22, 10],
  [20, 26],
  [20, 42],
  [22, 58],
  [40, 12],
  [38, 28],
  [38, 40],
  [40, 56],
  [50, 26],
  [50, 42],
];
const AWAY: [number, number][] = HOME.map(([x, y]) => [105 - x, 68 - y]);

interface Pose {
  id: number;
  cls: number;
  team: number;
  x: number;
  y: number;
}

function poses(t: number): Pose[] {
  const ballX = 52.5 + 30 * Math.sin(t / 9) + 6 * Math.sin(t / 2.3);
  const ballY = 34 + 20 * Math.sin(t / 6.1 + 1) * Math.cos(t / 11);
  const shift = (ballX - 52.5) * 0.35;
  const out: Pose[] = [];
  const team = (base: [number, number][], teamNo: number, idBase: number) =>
    base.forEach(([bx, by], i) => {
      const ph = i * 1.7 + teamNo * 0.9;
      const gk = i === 0;
      out.push({
        id: idBase + i,
        cls: gk ? 1 : 0,
        team: teamNo,
        x: bx + (gk ? shift * 0.2 : shift) + (gk ? 1 : 4) * Math.sin(t / 3.3 + ph),
        y: by + (gk ? 2 : 5) * Math.cos(t / 4.1 + ph) + (ballY - 34) * (gk ? 0.1 : 0.25),
      });
    });
  team(HOME, 0, 1);
  team(AWAY, 1, 12);
  out.push({ id: 30, cls: 2, team: -1, x: ballX - 8, y: ballY + 9 * Math.sin(t / 5) });
  out.push({ id: 99, cls: 3, team: -1, x: ballX, y: ballY });
  return out;
}

/** Fake broadcast camera: 48 m wide view centred on the ball, mild perspective. */
function project(p: Pose, camX: number) {
  const view = 48;
  const depth = p.y / 68; // 0 far touchline … 1 near touchline
  const sx = 1 - 0.18 * (1 - depth);
  const x = MOCK_WIDTH / 2 + ((p.x - camX) / view) * MOCK_WIDTH * sx;
  const foot = 150 + depth * 520;
  const h = p.cls === 3 ? 14 : 58 + depth * 46;
  const w = p.cls === 3 ? 14 : h * 0.42;
  return { x1: x - w / 2, y1: p.cls === 3 ? foot - h : foot - h, x2: x + w / 2, y2: foot };
}

export function mockTileFrame(f: number): TileFrame {
  const ms = (f * 1000) / MOCK_FPS;
  if (shotTypeAtMs(ms) !== 'main_wide') return { f, hq: null, objs: [] };
  const t = ms / 1000;
  const all = poses(t);
  const ball = all.at(-1);
  const camX = Math.min(105 - 24, Math.max(24, ball?.x ?? 52.5));
  const hq = hqAtMs(ms);
  const objs: NonNullable<TileFrame['objs']> = [];
  for (const p of all) {
    const b = project(p, camX);
    if (b.x2 < 0 || b.x1 > MOCK_WIDTH) continue;
    const conf = Math.round((0.72 + 0.25 * Math.abs(Math.sin(t / 2 + p.id))) * 100) / 100;
    objs.push([
      p.id,
      p.cls,
      p.team,
      Math.round(b.x1),
      Math.round(b.y1),
      Math.round(b.x2),
      Math.round(b.y2),
      conf,
      Math.round(p.x * 10) / 10,
      Math.round(p.y * 10) / 10,
    ]);
  }
  return { f, hq: Math.round(hq * 100) / 100, objs };
}

export function mockTile(tileNo: number, durationMs: number): Tile {
  const f0 = tileNo * FPT;
  const frameCount = Math.round((durationMs * MOCK_FPS) / 1000);
  const f1 = Math.min(f0 + FPT, frameCount);
  const frames: TileFrame[] = [];
  const shots: [number, number, ShotType][] = [];
  for (let f = f0; f < f1; f += 1) {
    frames.push(mockTileFrame(f));
    const type = shotTypeAtMs((f * 1000) / MOCK_FPS);
    const run = shots.at(-1);
    if (run && run[2] === type && run[1] === f) run[1] = f + 1;
    else shots.push([f, f + 1, type]);
  }
  return {
    v: 1,
    tile_no: tileNo,
    t0_ms: tileNo * MOCK_TILE_MS,
    t1_ms: Math.min(durationMs, (tileNo + 1) * MOCK_TILE_MS),
    fps: MOCK_FPS,
    shots,
    frames,
  };
}

export async function gzipJson(value: unknown): Promise<Uint8Array<ArrayBuffer>> {
  const body = new Response(JSON.stringify(value)).body;
  if (!body) throw new Error('no body');
  const gz = body.pipeThrough(new CompressionStream('gzip'));
  return new Uint8Array(await new Response(gz).arrayBuffer());
}

/** `/__mock/tiles/:videoId/000042.json.gz?d=<durationMs>` → gzipped tile response. */
export async function mockTileResponse(url: string): Promise<Response> {
  const u = new URL(url, 'http://localhost');
  const m = /\/(\d{6})\.json\.gz$/.exec(u.pathname);
  const durationMs = Number(u.searchParams.get('d') ?? 0);
  if (!m?.[1] || !(durationMs > 0)) return new Response(null, { status: 404 });
  const tileNo = Number(m[1]);
  if (tileNo * MOCK_TILE_MS >= durationMs) return new Response(null, { status: 404 });
  const body = await gzipJson(mockTile(tileNo, durationMs));
  return new Response(body.slice().buffer, {
    status: 200,
    headers: { 'Content-Type': 'application/gzip', 'Cache-Control': 'max-age=3600' },
  });
}

/** fetch that serves mock tiles in-page (dev player without the MSW worker). */
export const mockTileFetch: typeof fetch = (input) =>
  mockTileResponse(
    typeof input === 'string' ? input : input instanceof URL ? input.href : input.url,
  );

export function playerHandlers() {
  return [
    http.get('*/__mock/tiles/:videoId/:file', async ({ request }) => {
      const res = await mockTileResponse(request.url);
      if (!res.ok) return new HttpResponse(null, { status: res.status });
      return new HttpResponse(await res.arrayBuffer(), {
        status: 200,
        headers: { 'Content-Type': 'application/gzip' },
      });
    }),
  ];
}
