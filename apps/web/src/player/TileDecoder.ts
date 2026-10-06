import type { Tile } from '@/api/types';
import { decodeTile, TileFormatError } from './tileCodec';

export interface TileDecoder {
  decode(buf: ArrayBuffer, signal?: AbortSignal): Promise<Tile>;
  dispose?(): void;
}

export interface DecodeRequest {
  id: number;
  buf: ArrayBuffer;
}
export type DecodeResponse =
  { id: number; ok: true; tile: Tile } | { id: number; ok: false; error: string };

/** The subset of Worker used (tests inject a fake). */
export interface WorkerLike {
  postMessage(msg: DecodeRequest, transfer: Transferable[]): void;
  onmessage: ((e: MessageEvent<DecodeResponse>) => void) | null;
  terminate(): void;
}

function abortError(): DOMException {
  return new DOMException('Tile decode aborted', 'AbortError');
}

/** Decodes in a dedicated Web Worker; the buffer is transferred (zero-copy). */
export class WorkerTileDecoder implements TileDecoder {
  private readonly worker: WorkerLike;
  private seq = 0;
  private readonly pending = new Map<
    number,
    { resolve: (t: Tile) => void; reject: (e: unknown) => void }
  >();

  constructor(
    factory: () => WorkerLike = () =>
      new Worker(new URL('./tile.worker.ts', import.meta.url), {
        type: 'module',
        name: 'pv-tile-decoder',
      }),
  ) {
    this.worker = factory();
    this.worker.onmessage = (e) => {
      const msg = e.data;
      const p = this.pending.get(msg.id);
      if (!p) return; // aborted meanwhile
      this.pending.delete(msg.id);
      if (msg.ok) p.resolve(msg.tile);
      else p.reject(new TileFormatError(msg.error));
    };
  }

  decode(buf: ArrayBuffer, signal?: AbortSignal): Promise<Tile> {
    if (signal?.aborted) return Promise.reject(abortError());
    const id = ++this.seq;
    return new Promise<Tile>((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      signal?.addEventListener(
        'abort',
        () => {
          if (this.pending.delete(id)) reject(abortError());
        },
        { once: true },
      );
      this.worker.postMessage({ id, buf }, [buf]);
    });
  }

  dispose(): void {
    this.worker.terminate();
    for (const p of this.pending.values()) p.reject(abortError());
    this.pending.clear();
  }
}

/** Main-thread decoder (tests, environments without Worker). */
export class InlineTileDecoder implements TileDecoder {
  async decode(buf: ArrayBuffer, signal?: AbortSignal): Promise<Tile> {
    const tile = await decodeTile(buf);
    if (signal?.aborted) throw abortError();
    return tile;
  }
}

export function createTileDecoder(): TileDecoder {
  return typeof Worker === 'function' ? new WorkerTileDecoder() : new InlineTileDecoder();
}
