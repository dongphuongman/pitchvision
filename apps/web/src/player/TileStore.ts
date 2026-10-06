import type { ShotType, Tile, TileFrame } from '@/api/types';
import { DEFAULT_TILE_MS, framesPerTile, tileForFrame, tileUrl } from './frames';
import { shotTypeInRuns } from './shots';
import type { TileDecoder } from './TileDecoder';

/** A tile indexed for O(1) frame lookup. */
export interface DecodedTile {
  tileNo: number;
  /** First frame of the tile (inclusive) and end (exclusive). */
  f0: number;
  f1: number;
  /** `frames[f - f0]`; undefined = no record for that frame. */
  frames: (TileFrame | undefined)[];
  shots: readonly (readonly [number, number, ShotType])[];
  tile: Tile;
}

export interface FrameLookup {
  frame: TileFrame | undefined;
  /** Shot type from the tile's runs; undefined while the tile isn't loaded. */
  shot: ShotType | undefined;
  loaded: boolean;
}

export interface TileStoreOptions {
  /** Manifest `tile_url_template` (`{tile_no}` → 6 digits). */
  urlTemplate: string;
  tileCount: number;
  fps: number;
  tileMs?: number;
  /** An instance, or a factory called lazily on the first load (and again after close()). */
  decoder: TileDecoder | (() => TileDecoder);
  fetch?: typeof fetch;
  /** LRU capacity (AD §11.1: 30 tiles ≈ 5 min). */
  capacity?: number;
  /** Prefetch radius around the playhead tile (AD §11.1: ±2). */
  prefetch?: number;
  /** Hook to apply user corrections before indexing (M2, SCR-22). */
  transform?: (tile: Tile) => Tile;
  /** After a failed load, don't retry the same tile for this long. */
  retryAfterMs?: number;
  now?: () => number;
}

interface InFlight {
  promise: Promise<DecodedTile>;
  controller: AbortController;
}

function isAbort(e: unknown): boolean {
  return e instanceof DOMException && e.name === 'AbortError';
}

/**
 * TileStore (AD §11.1): fetches playback tiles from the manifest template, decodes them in a Web
 * Worker, keeps an LRU of `capacity` tiles and prefetches ±`prefetch` tiles around the playhead.
 * Loads for the same tile are deduped; moving the playhead (seek) aborts loads that fell out of
 * the new window, and the target tile is requested first.
 */
export class TileStore {
  readonly fpt: number;
  private readonly opts: Required<Omit<TileStoreOptions, 'transform' | 'fetch'>> &
    Pick<TileStoreOptions, 'transform' | 'fetch'>;
  private readonly cache = new Map<number, DecodedTile>();
  private readonly inflight = new Map<number, InFlight>();
  private readonly failedAt = new Map<number, number>();
  private readonly listeners = new Set<(tileNo: number) => void>();
  private window: { center: number; lo: number; hi: number } | null = null;
  private decoder: TileDecoder | null = null;

  constructor(options: TileStoreOptions) {
    this.opts = {
      tileMs: DEFAULT_TILE_MS,
      capacity: 30,
      prefetch: 2,
      retryAfterMs: 2_000,
      now: () => Date.now(),
      ...options,
    };
    this.fpt = framesPerTile(this.opts.tileMs, this.opts.fps);
  }

  tileForFrame(f: number): number {
    return tileForFrame(f, this.fpt);
  }

  url(tileNo: number): string {
    return tileUrl(this.opts.urlTemplate, tileNo);
  }

  /** Cached tile, marked most recently used. */
  get(tileNo: number): DecodedTile | undefined {
    const t = this.cache.get(tileNo);
    if (t) {
      this.cache.delete(tileNo);
      this.cache.set(tileNo, t);
    }
    return t;
  }

  /** Data for one frame (no fetch; call setPlayhead to load). */
  frame(f: number): FrameLookup {
    const t = this.get(this.tileForFrame(f));
    if (!t) return { frame: undefined, shot: undefined, loaded: false };
    return { frame: t.frames[f - t.f0], shot: shotTypeInRuns(t.shots, f), loaded: true };
  }

  /** Tiles in LRU order (oldest first). */
  cachedTiles(): number[] {
    return [...this.cache.keys()];
  }

  inflightTiles(): number[] {
    return [...this.inflight.keys()];
  }

  /** Fires when a tile has been decoded and cached. */
  onTile(listener: (tileNo: number) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  /**
   * Playhead moved to frame `f`: keep the window [t-p, t+p] loaded (target first, then forward-
   * biased neighbours) and abort loads outside it. Cheap when the tile didn't change.
   */
  setPlayhead(f: number): void {
    const center = this.tileForFrame(f);
    if (this.window?.center === center) return;
    this.moveWindow(center);
  }

  /** A seek always re-evaluates the window, even within the same tile. */
  seek(f: number): void {
    this.moveWindow(this.tileForFrame(f));
  }

  /** Load (or join the in-flight load of) one tile. */
  load(tileNo: number): Promise<DecodedTile> {
    const cached = this.get(tileNo);
    if (cached) return Promise.resolve(cached);
    const existing = this.inflight.get(tileNo);
    if (existing) return existing.promise;

    const controller = new AbortController();
    const promise = this.fetchAndDecode(tileNo, controller.signal).then(
      (decoded) => {
        if (this.inflight.get(tileNo)?.controller === controller) this.inflight.delete(tileNo);
        if (controller.signal.aborted) throw new DOMException('aborted', 'AbortError');
        this.insert(decoded);
        this.failedAt.delete(tileNo);
        for (const l of this.listeners) l(tileNo);
        return decoded;
      },
      (e: unknown) => {
        if (this.inflight.get(tileNo)?.controller === controller) this.inflight.delete(tileNo);
        if (!isAbort(e)) this.failedAt.set(tileNo, this.opts.now());
        throw e;
      },
    );
    this.inflight.set(tileNo, { promise, controller });
    return promise;
  }

  /**
   * Abort every load and release the decoder (terminates its Worker when created from a
   * factory). Cached tiles stay; the store keeps working — the next load re-creates the decoder.
   */
  close(): void {
    for (const f of this.inflight.values()) f.controller.abort();
    this.inflight.clear();
    this.window = null;
    if (this.decoder && typeof this.opts.decoder === 'function') {
      this.decoder.dispose?.();
      this.decoder = null;
    }
  }

  private getDecoder(): TileDecoder {
    const d = this.opts.decoder;
    this.decoder ??= typeof d === 'function' ? d() : d;
    return this.decoder;
  }

  /* -------------------------------------------------------------------- internals */

  private moveWindow(center: number): void {
    const p = this.opts.prefetch;
    const last = Math.max(0, this.opts.tileCount - 1);
    const lo = Math.max(0, center - p);
    const hi = Math.min(last, center + p);
    this.window = { center, lo, hi };

    for (const [n, f] of this.inflight) {
      if (n < lo || n > hi) {
        f.controller.abort();
        this.inflight.delete(n);
      }
    }

    // Target first, then +1, −1, +2, −2 … (playback moves forward).
    const order = [center];
    for (let d = 1; d <= p; d += 1) order.push(center + d, center - d);
    const now = this.opts.now();
    for (const n of order) {
      if (n < lo || n > hi || this.cache.has(n) || this.inflight.has(n)) continue;
      const failed = this.failedAt.get(n);
      if (failed !== undefined && now - failed < this.opts.retryAfterMs) continue;
      this.load(n).catch(() => undefined);
    }
  }

  private async fetchAndDecode(tileNo: number, signal: AbortSignal): Promise<DecodedTile> {
    const fetchImpl = this.opts.fetch ?? globalThis.fetch;
    const res = await fetchImpl(this.url(tileNo), { signal });
    if (!res.ok) throw new Error(`Tile ${tileNo}: HTTP ${res.status}`);
    const buf = await res.arrayBuffer();
    let tile = await this.getDecoder().decode(buf, signal);
    if (this.opts.transform) tile = this.opts.transform(tile);
    return this.index(tileNo, tile);
  }

  private index(tileNo: number, tile: Tile): DecodedTile {
    const f0 = tileNo * this.fpt;
    const frames = new Array<TileFrame | undefined>(this.fpt);
    for (const fr of tile.frames) {
      const i = fr.f - f0;
      if (i >= 0 && i < this.fpt) frames[i] = fr;
    }
    return { tileNo, f0, f1: f0 + this.fpt, frames, shots: tile.shots ?? [], tile };
  }

  private insert(t: DecodedTile): void {
    this.cache.delete(t.tileNo);
    this.cache.set(t.tileNo, t);
    while (this.cache.size > this.opts.capacity) {
      // Evict the least recently used tile outside the playhead window (fallback: the oldest).
      const w = this.window;
      let victim: number | undefined;
      for (const n of this.cache.keys()) {
        if (!w || n < w.lo || n > w.hi) {
          victim = n;
          break;
        }
      }
      victim ??= this.cache.keys().next().value;
      if (victim === undefined) break;
      this.cache.delete(victim);
    }
  }
}
