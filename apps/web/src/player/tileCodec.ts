import type { Tile } from '@/api/types';

/** Tile format version this client understands (packages/schemas tiles.TILE_VERSION). */
export const TILE_VERSION = 1;

export class TileFormatError extends Error {
  override readonly name = 'TileFormatError';
}

/** Object classes in `objs[i][1]` (packages/schemas tiles.ObjCls). */
export const OBJ_CLS = { player: 0, goalkeeper: 1, referee: 2, ball: 3 } as const;

/** Positional fields of an object row: [track_id, cls, team, x1, y1, x2, y2, conf, px, py]. */
export const OBJ = {
  trackId: 0,
  cls: 1,
  team: 2,
  x1: 3,
  y1: 4,
  x2: 5,
  y2: 6,
  conf: 7,
  px: 8,
  py: 9,
} as const;

function isGzip(bytes: Uint8Array): boolean {
  return bytes.length > 2 && bytes[0] === 0x1f && bytes[1] === 0x8b;
}

/** gzip → text with DecompressionStream; plain bytes pass through (CDN already decoded). */
export async function tileBytesToText(buf: ArrayBuffer): Promise<string> {
  const bytes = new Uint8Array(buf);
  if (!isGzip(bytes)) return new TextDecoder().decode(bytes);
  const body = new Response(bytes).body;
  if (!body) throw new TileFormatError('Empty tile body');
  return new Response(body.pipeThrough(new DecompressionStream('gzip'))).text();
}

function fail(msg: string): never {
  throw new TileFormatError(msg);
}

/** Structural check of a parsed tile (cheap: shape of the envelope + first object row). */
export function parseTile(value: unknown): Tile {
  if (typeof value !== 'object' || value === null) fail('Tile is not an object');
  const t = value as Partial<Tile>;
  const v = t.v ?? 1;
  if (v !== TILE_VERSION) fail(`Unsupported tile version ${String(v)}`);
  if (typeof t.tile_no !== 'number' || !Array.isArray(t.frames) || typeof t.fps !== 'number') {
    fail('Tile is missing tile_no / fps / frames');
  }
  const frames = t.frames;
  const sample: unknown = frames.find((f) => f.objs && f.objs.length > 0)?.objs?.[0];
  if (sample && (!Array.isArray(sample) || sample.length !== 10)) {
    fail('Tile object rows must have 10 fields');
  }
  if (t.shots !== undefined && !Array.isArray(t.shots)) fail('Tile shots must be an array');
  return t as Tile;
}

/** Worker entry point and main-thread fallback: bytes → typed Tile. */
export async function decodeTile(buf: ArrayBuffer): Promise<Tile> {
  const text = await tileBytesToText(buf);
  let json: unknown;
  try {
    json = JSON.parse(text);
  } catch {
    fail('Tile is not valid JSON');
  }
  return parseTile(json);
}
