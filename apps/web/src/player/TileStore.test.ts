import type { Tile } from '@/api/types';
import { gzipJson, mockTile } from '@/mocks/player';
import { decodeTile, parseTile, TileFormatError } from './tileCodec';
import {
  InlineTileDecoder,
  WorkerTileDecoder,
  type DecodeRequest,
  type DecodeResponse,
  type TileDecoder,
  type WorkerLike,
} from './TileDecoder';
import { TileStore } from './TileStore';

const FPT = 250;

function tileJson(n: number): Tile {
  return {
    v: 1,
    tile_no: n,
    t0_ms: n * 10_000,
    t1_ms: (n + 1) * 10_000,
    fps: 25,
    shots: [
      [n * FPT, n * FPT + 100, 'main_wide'],
      [n * FPT + 100, (n + 1) * FPT, 'replay'],
    ],
    frames: [{ f: n * FPT + 5, hq: 0.9, objs: [[7, 0, 0, 10, 20, 30, 80, 0.9, 50, 30]] }],
  };
}

/** fetch that the test resolves explicitly; records URLs and abort signals. */
function controlledFetch() {
  const calls: { url: string; signal: AbortSignal; resolve: () => void }[] = [];
  const fetchImpl = vi.fn((input: string, init?: RequestInit) => {
    const url = input;
    const signal = init?.signal ?? new AbortController().signal;
    return new Promise<Response>((resolve, reject) => {
      const n = Number(/(\d{6})/.exec(url)?.[1]);
      calls.push({
        url,
        signal,
        resolve: () => resolve(new Response(JSON.stringify(tileJson(n)))),
      });
      signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
    });
  });
  return { fetchImpl, calls };
}

/** Decoder stub standing in for the worker: parses JSON, counts calls. */
function fakeDecoder(): TileDecoder & { calls: number } {
  const d = {
    calls: 0,
    decode: (buf: ArrayBuffer) => {
      d.calls += 1;
      return Promise.resolve(parseTile(JSON.parse(new TextDecoder().decode(buf))));
    },
  };
  return d;
}

function makeStore(over: Partial<ConstructorParameters<typeof TileStore>[0]> = {}) {
  const { fetchImpl, calls } = controlledFetch();
  const decoder = fakeDecoder();
  const store = new TileStore({
    urlTemplate: 'https://tiles.test/v/{tile_no}.json.gz?sig=x',
    tileCount: 100,
    fps: 25,
    decoder,
    fetch: fetchImpl as unknown as typeof fetch,
    ...over,
  });
  const tilesOf = () => calls.map((c) => Number(/(\d{6})/.exec(c.url)?.[1]));
  return { store, calls, decoder, fetchImpl, tilesOf };
}

const settle = () => new Promise((r) => setTimeout(r, 0));

describe('TileStore', () => {
  it('prefetches ±2 tiles around the playhead, target first then forward-biased', () => {
    const { store, tilesOf, calls } = makeStore();
    store.setPlayhead(10 * FPT + 3);
    expect(tilesOf()).toEqual([10, 11, 9, 12, 8]);
    expect(calls[0]?.url).toBe('https://tiles.test/v/000010.json.gz?sig=x');
    // Same tile again: nothing new.
    store.setPlayhead(10 * FPT + 200);
    expect(tilesOf()).toHaveLength(5);
  });

  it('clamps the window at the start and end of the video', () => {
    const { store, tilesOf } = makeStore({ tileCount: 3 });
    store.setPlayhead(0);
    expect(tilesOf()).toEqual([0, 1, 2]);
  });

  it('decodes, indexes frames and shot runs, and notifies', async () => {
    const { store, calls, decoder } = makeStore({ prefetch: 0 });
    const loaded: number[] = [];
    store.onTile((n) => loaded.push(n));
    expect(store.frame(3 * FPT + 5)).toEqual({ frame: undefined, shot: undefined, loaded: false });
    store.setPlayhead(3 * FPT);
    calls[0]?.resolve();
    await settle();
    expect(loaded).toEqual([3]);
    expect(decoder.calls).toBe(1);
    const hit = store.frame(3 * FPT + 5);
    expect(hit.loaded).toBe(true);
    expect(hit.frame?.objs?.[0]?.[0]).toBe(7);
    expect(hit.shot).toBe('main_wide');
    expect(store.frame(3 * FPT + 150)).toMatchObject({ frame: undefined, shot: 'replay' });
  });

  it('dedupes concurrent loads of the same tile', async () => {
    const { store, calls } = makeStore();
    const a = store.load(4);
    const b = store.load(4);
    expect(calls).toHaveLength(1);
    calls[0]?.resolve();
    expect(await a).toBe(await b);
    // Cached afterwards: no fetch.
    await store.load(4);
    expect(calls).toHaveLength(1);
  });

  it('a seek aborts loads outside the new window and loads the target first', () => {
    const { store, calls, tilesOf } = makeStore();
    store.setPlayhead(10 * FPT);
    const before = [...calls];
    store.seek(50 * FPT);
    for (const c of before) expect(c.signal.aborted).toBe(true);
    expect(tilesOf().slice(5)).toEqual([50, 51, 49, 52, 48]);
    expect(store.inflightTiles().sort((a, b) => a - b)).toEqual([48, 49, 50, 51, 52]);
  });

  it('keeps loads that are still inside the window after a short seek', () => {
    const { store, calls } = makeStore();
    store.setPlayhead(10 * FPT);
    store.seek(11 * FPT);
    const byTile = (n: number) => calls.find((c) => c.url.includes(String(n).padStart(6, '0')));
    expect(byTile(8)?.signal.aborted).toBe(true); // 8 left [9, 13]
    expect(byTile(9)?.signal.aborted).toBe(false);
    expect(byTile(12)?.signal.aborted).toBe(false);
    expect(byTile(13)).toBeDefined();
  });

  it('drops a tile whose load was aborted mid-flight', async () => {
    const { store, calls } = makeStore({ prefetch: 0 });
    store.setPlayhead(5 * FPT);
    store.seek(40 * FPT);
    calls[0]?.resolve(); // too late, aborted
    await settle();
    expect(store.cachedTiles()).toEqual([]);
  });

  it('evicts the least recently used tile outside the window (LRU)', async () => {
    const { store, calls } = makeStore({ capacity: 3, prefetch: 0 });
    for (const n of [1, 2, 3]) {
      const p = store.load(n);
      calls.at(-1)?.resolve();
      await p;
    }
    expect(store.cachedTiles()).toEqual([1, 2, 3]);
    store.get(1); // touch → 2 is now the oldest
    const p = store.load(4);
    calls.at(-1)?.resolve();
    await p;
    expect(store.cachedTiles()).toEqual([3, 1, 4]);
  });

  it('never evicts the playhead tile even when it is the oldest', async () => {
    const { store, calls } = makeStore({ capacity: 2, prefetch: 0 });
    store.setPlayhead(7 * FPT);
    calls[0]?.resolve();
    await settle();
    for (const n of [20, 21]) {
      const p = store.load(n);
      calls.at(-1)?.resolve();
      await p;
    }
    expect(store.cachedTiles()).toContain(7);
    expect(store.cachedTiles()).toHaveLength(2);
  });

  it('backs off after a failed tile', async () => {
    let now = 0;
    const failing = vi.fn(() => Promise.resolve(new Response(null, { status: 503 })));
    const store = new TileStore({
      urlTemplate: '/t/{tile_no}',
      tileCount: 10,
      fps: 25,
      prefetch: 0,
      decoder: fakeDecoder(),
      fetch: failing,
      now: () => now,
    });
    store.setPlayhead(0);
    await settle();
    store.seek(0);
    expect(failing).toHaveBeenCalledTimes(1);
    now = 2_500;
    store.seek(0);
    expect(failing).toHaveBeenCalledTimes(2);
  });

  it('close() aborts loads and releases a factory-made decoder', async () => {
    const dispose = vi.fn();
    const factory = vi.fn(() => ({ decode: () => new Promise<Tile>(() => undefined), dispose }));
    const { store, calls } = makeStore({ decoder: factory });
    expect(factory).not.toHaveBeenCalled(); // lazy: no Worker until the first tile
    store.setPlayhead(0);
    calls[0]?.resolve(); // tile 0 reaches the (hanging) decoder
    await settle();
    expect(factory).toHaveBeenCalledTimes(1);
    store.close();
    expect(calls.every((c) => c.signal.aborted)).toBe(true);
    expect(store.inflightTiles()).toEqual([]);
    expect(dispose).toHaveBeenCalledTimes(1);
  });
});

describe('tile decoding', () => {
  it('gunzips with DecompressionStream and parses (the worker code path)', async () => {
    const tile = mockTile(2, 60_000);
    const gz = await gzipJson(tile);
    expect(gz[0]).toBe(0x1f);
    const decoded = await decodeTile(gz.buffer);
    expect(decoded.tile_no).toBe(2);
    expect(decoded.frames).toHaveLength(250);
    expect(decoded.frames[0]?.f).toBe(500);
  });

  it('accepts already-decompressed JSON', async () => {
    const buf = new TextEncoder().encode(JSON.stringify(tileJson(1))).buffer;
    expect((await decodeTile(buf)).tile_no).toBe(1);
  });

  it('rejects malformed tiles', async () => {
    await expect(decodeTile(new TextEncoder().encode('nope').buffer)).rejects.toBeInstanceOf(
      TileFormatError,
    );
    expect(() => parseTile({ v: 2, tile_no: 0, fps: 25, frames: [] })).toThrow(/version/);
    expect(() => parseTile({ tile_no: 0, fps: 25, frames: [{ f: 0, objs: [[1, 2, 3]] }] })).toThrow(
      /10 fields/,
    );
  });

  it('InlineTileDecoder honours abort', async () => {
    const ac = new AbortController();
    ac.abort();
    const buf = new TextEncoder().encode(JSON.stringify(tileJson(0))).buffer;
    await expect(new InlineTileDecoder().decode(buf, ac.signal)).rejects.toMatchObject({
      name: 'AbortError',
    });
  });
});

describe('WorkerTileDecoder', () => {
  function fakeWorker() {
    const posted: { msg: DecodeRequest; transfer: Transferable[] }[] = [];
    const terminate = vi.fn();
    const w: WorkerLike & {
      posted: typeof posted;
      reply: (r: DecodeResponse) => void;
      terminated: typeof terminate;
    } = {
      posted,
      onmessage: null,
      postMessage: (msg, transfer) => posted.push({ msg, transfer }),
      terminate,
      terminated: terminate,
      reply: (r) => w.onmessage?.(new MessageEvent('message', { data: r })),
    };
    return w;
  }

  it('posts the buffer as a transferable and resolves with the reply', async () => {
    const w = fakeWorker();
    const d = new WorkerTileDecoder(() => w);
    const buf = new ArrayBuffer(8);
    const p = d.decode(buf);
    expect(w.posted[0]?.transfer).toEqual([buf]);
    const id = w.posted[0]?.msg.id ?? -1;
    w.reply({ id, ok: true, tile: tileJson(3) });
    expect((await p).tile_no).toBe(3);
  });

  it('maps worker errors and aborts; ignores late replies', async () => {
    const w = fakeWorker();
    const d = new WorkerTileDecoder(() => w);
    const failing = d.decode(new ArrayBuffer(1));
    w.reply({ id: w.posted[0]?.msg.id ?? -1, ok: false, error: 'bad gzip' });
    await expect(failing).rejects.toThrow('bad gzip');

    const ac = new AbortController();
    const aborted = d.decode(new ArrayBuffer(1), ac.signal);
    ac.abort();
    await expect(aborted).rejects.toMatchObject({ name: 'AbortError' });
    w.reply({ id: w.posted[1]?.msg.id ?? -1, ok: true, tile: tileJson(0) }); // no throw

    d.dispose();
    expect(w.terminated).toHaveBeenCalled();
  });
});
