/**
 * Browser → object storage multipart upload (AD §8.1, proven in spike SP-07 on real R2).
 *
 * - Parts go straight to storage through presigned URLs; the API only creates, signs and completes.
 * - Part size is fixed by the API (16 MiB) and never changes on resume — R2 rejects unequal parts.
 * - 6 parts in flight (SP-07: 8 streams ≈ 21 MB/s vs 4 ≈ 7 MB/s on a home uplink), 5 attempts per part
 *   with exponential backoff, URLs signed in batches so none expire during a slow upload.
 * - Resume: `file name + size + lastModified` → video id in localStorage; re-selecting the same file
 *   asks the API which parts storage already holds (ListParts) and sends only the rest.
 *
 * Framework-free so it can be tested with fake transports; React binds to it in `useUploads`.
 */
import type { CompletedPart, PartUrl, UploadCreated, UploadState } from '@/api/uploads';

export type UploadPhase = 'starting' | 'uploading' | 'completing' | 'done' | 'failed' | 'cancelled';

export interface UploadItem {
  /** Stable client id (the video id is only known after `createUpload`). */
  id: string;
  videoId: string | null;
  filename: string;
  size: number;
  /** Bytes confirmed by storage + bytes of parts in flight. */
  sent: number;
  bytesPerSec: number;
  etaS: number | null;
  phase: UploadPhase;
  autoAnalyze: boolean;
  resumedParts: number;
  jobId: string | null;
  error: unknown;
}

export interface UploadResult {
  videoId: string;
  jobId: string | null;
}

export interface UploadApi {
  createUpload(body: {
    filename: string;
    size: number;
    mime: string;
    title?: string | null;
  }): Promise<UploadCreated>;
  signParts(videoId: string, partNumbers: number[]): Promise<PartUrl[]>;
  getUploadState(videoId: string): Promise<UploadState>;
  completeUpload(videoId: string, parts: CompletedPart[]): Promise<unknown>;
  abortUpload(videoId: string): Promise<void>;
  createJob(videoId: string, config?: Record<string, unknown>): Promise<{ id: string }>;
}

/** PUT one part; resolves with the ETag storage returned. */
export type PutPart = (
  url: string,
  body: Blob,
  onProgress: (loaded: number) => void,
  signal: AbortSignal,
) => Promise<string>;

export interface UploadDeps {
  api: UploadApi;
  putPart: PutPart;
  storage?: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'> | null;
  now?: () => number;
  sleep?: (ms: number, signal: AbortSignal) => Promise<void>;
}

export interface UploadOptions {
  concurrency?: number;
  maxAttempts?: number;
  signBatch?: number;
}

type Listener = (items: UploadItem[]) => void;

export class UploadCancelled extends Error {
  constructor() {
    super('Upload cancelled');
    this.name = 'UploadCancelled';
  }
}

export const resumeKey = (f: Pick<File, 'name' | 'size' | 'lastModified'>) =>
  `pv:upload:${f.name}:${f.size}:${f.lastModified}`;

const defaultSleep = (ms: number, signal: AbortSignal) =>
  new Promise<void>((resolve, reject) => {
    const t = setTimeout(resolve, ms);
    signal.addEventListener('abort', () => {
      clearTimeout(t);
      reject(new UploadCancelled());
    });
  });

function safeStorage(): UploadDeps['storage'] {
  try {
    return globalThis.localStorage;
  } catch {
    return null;
  }
}

export class UploadManager {
  private items = new Map<string, UploadItem>();
  private controllers = new Map<string, AbortController>();
  private listeners = new Set<Listener>();
  private readonly concurrency: number;
  private readonly maxAttempts: number;
  private readonly signBatch: number;
  private readonly now: () => number;
  private readonly sleep: NonNullable<UploadDeps['sleep']>;
  private readonly storage: UploadDeps['storage'];
  private seq = 0;

  constructor(
    private readonly deps: UploadDeps,
    opts: UploadOptions = {},
  ) {
    this.concurrency = opts.concurrency ?? 6;
    this.maxAttempts = opts.maxAttempts ?? 5;
    this.signBatch = opts.signBatch ?? 32;
    this.now = deps.now ?? (() => performance.now());
    this.sleep = deps.sleep ?? defaultSleep;
    this.storage = deps.storage === undefined ? safeStorage() : deps.storage;
  }

  list(): UploadItem[] {
    return [...this.items.values()];
  }

  get(id: string): UploadItem | undefined {
    return this.items.get(id);
  }

  /** Uploads still moving bytes — the sign-out guard (DLG-03) and `beforeunload` use this. */
  activeCount(): number {
    return this.list().filter((i) => ['starting', 'uploading', 'completing'].includes(i.phase))
      .length;
  }

  subscribe(fn: Listener): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  /** Abort everything and forget all items (tests; sign-out of another user). */
  reset(): void {
    for (const c of this.controllers.values()) c.abort();
    this.controllers.clear();
    this.items.clear();
    this.emit();
  }

  /** Forget finished / failed / cancelled items (the tray's "clear"). */
  dismiss(id: string): void {
    const it = this.items.get(id);
    if (it && !['starting', 'uploading', 'completing'].includes(it.phase)) {
      this.items.delete(id);
      this.emit();
    }
  }

  /** E14: stop sending, abort the multipart upload server-side, forget the resume entry. */
  async cancel(id: string): Promise<void> {
    const it = this.items.get(id);
    if (!it || !['starting', 'uploading', 'completing'].includes(it.phase)) return;
    this.controllers.get(id)?.abort();
    this.patch(id, { phase: 'cancelled', etaS: null, bytesPerSec: 0 });
    if (it.videoId) {
      this.storage?.removeItem(this.keyFor(id));
      await this.deps.api.abortUpload(it.videoId).catch(() => undefined); // best effort; lifecycle cleans up
    }
  }

  /**
   * Start (or resume) uploading `file`. Returns the client id at once; `done` settles when the upload
   * (and, with `autoAnalyze`, the job creation) finishes.
   */
  start(
    file: File,
    {
      title,
      autoAnalyze,
      jobConfig,
    }: { title: string | null; autoAnalyze: boolean; jobConfig?: Record<string, unknown> },
  ): { id: string; done: Promise<UploadResult> } {
    const id = `u${++this.seq}`;
    const ctrl = new AbortController();
    this.controllers.set(id, ctrl);
    this.keys.set(id, resumeKey(file));
    this.items.set(id, {
      id,
      videoId: null,
      filename: file.name,
      size: file.size,
      sent: 0,
      bytesPerSec: 0,
      etaS: null,
      phase: 'starting',
      autoAnalyze,
      resumedParts: 0,
      jobId: null,
      error: null,
    });
    this.emit();
    const done = this.run(id, file, title, autoAnalyze, jobConfig, ctrl.signal).then(
      (res) => {
        this.controllers.delete(id);
        return res;
      },
      (err: unknown) => {
        this.controllers.delete(id);
        if (!(err instanceof UploadCancelled) && !ctrl.signal.aborted) {
          this.patch(id, { phase: 'failed', error: err, etaS: null, bytesPerSec: 0 });
        }
        throw err;
      },
    );
    return { id, done };
  }

  /* ------------------------------------------------------------------ internals */

  private keys = new Map<string, string>();

  private keyFor(id: string): string {
    return this.keys.get(id) ?? '';
  }

  private emit(): void {
    const snapshot = this.list();
    for (const l of this.listeners) l(snapshot);
  }

  private patch(id: string, patch: Partial<UploadItem>): void {
    const it = this.items.get(id);
    if (!it) return;
    this.items.set(id, { ...it, ...patch });
    this.emit();
  }

  private async session(
    file: File,
    title: string | null,
    key: string,
  ): Promise<{
    videoId: string;
    partSize: number;
    partCount: number;
    stored: Map<number, string>;
    first: PartUrl[];
  }> {
    const saved = this.storage?.getItem(key);
    if (saved) {
      try {
        const st = await this.deps.api.getUploadState(saved);
        const stored = new Map<number, string>();
        for (const p of st.parts) {
          const expected = Math.min(st.part_size, file.size - (p.part_number - 1) * st.part_size);
          if (p.size === expected) stored.set(p.part_number, p.etag);
        }
        return {
          videoId: saved,
          partSize: st.part_size,
          partCount: st.part_count,
          stored,
          first: [],
        };
      } catch {
        this.storage?.removeItem(key); // finished, aborted or deleted elsewhere → start over
      }
    }
    const created = await this.deps.api.createUpload({
      filename: file.name,
      size: file.size,
      mime: file.type || 'application/octet-stream',
      title,
    });
    this.storage?.setItem(key, created.video_id);
    return {
      videoId: created.video_id,
      partSize: created.part_size,
      partCount: created.part_count,
      stored: new Map(),
      first: created.parts,
    };
  }

  private async run(
    id: string,
    file: File,
    title: string | null,
    autoAnalyze: boolean,
    jobConfig: Record<string, unknown> | undefined,
    signal: AbortSignal,
  ): Promise<UploadResult> {
    const key = this.keyFor(id);
    // a function, not `signal.aborted` inline: TS narrows the property and can't see it change across awaits
    const aborted = () => signal.aborted;
    const s = await this.session(file, title, key);
    if (aborted()) throw new UploadCancelled();
    const { videoId, partSize, partCount, stored } = s;
    const etags = new Map(stored);
    const urls = new Map(s.first.map((p) => [p.part_number, p.url]));
    const todo: number[] = [];
    for (let n = 1; n <= partCount; n++) if (!etags.has(n)) todo.push(n);
    const partLen = (n: number) => Math.min(partSize, file.size - (n - 1) * partSize);

    let confirmed = [...etags.keys()].reduce((a, n) => a + partLen(n), 0);
    const inflight = new Map<number, number>();
    const t0 = this.now();
    const startBytes = confirmed;
    let lastEmit = 0;
    const progress = (force = false) => {
      const t = this.now();
      if (!force && t - lastEmit < 200) return;
      lastEmit = t;
      const sent = Math.min(
        file.size,
        confirmed + [...inflight.values()].reduce((a, b) => a + b, 0),
      );
      const elapsed = (t - t0) / 1000;
      const rate = elapsed > 0.5 ? (sent - startBytes) / elapsed : 0;
      this.patch(id, {
        sent,
        bytesPerSec: rate,
        etaS: rate > 0 ? Math.max(0, Math.round((file.size - sent) / rate)) : null,
      });
    };
    this.patch(id, { videoId, phase: 'uploading', resumedParts: stored.size, sent: confirmed });

    const urlFor = async (n: number): Promise<string> => {
      const cached = urls.get(n);
      if (cached) return cached;
      const batch = [
        n,
        ...todo.filter((m) => m !== n && !urls.has(m)).slice(0, this.signBatch - 1),
      ];
      for (const p of await this.deps.api.signParts(videoId, batch)) urls.set(p.part_number, p.url);
      const url = urls.get(n);
      if (!url) throw new Error(`No presigned URL for part ${n}`);
      return url;
    };

    const worker = async () => {
      for (let n = todo.shift(); n !== undefined; n = todo.shift()) {
        const blob = file.slice((n - 1) * partSize, (n - 1) * partSize + partLen(n));
        for (let attempt = 1; ; attempt++) {
          if (aborted()) throw new UploadCancelled();
          try {
            const part = n;
            const etag = await this.deps.putPart(
              await urlFor(part),
              blob,
              (loaded) => {
                inflight.set(part, loaded);
                progress();
              },
              signal,
            );
            if (!etag)
              throw new Error('Storage did not expose the ETag header (bucket CORS ExposeHeaders)');
            inflight.delete(part);
            etags.set(part, etag);
            confirmed += blob.size;
            progress(true);
            break;
          } catch (err) {
            inflight.delete(n);
            if (aborted()) throw new UploadCancelled();
            if (attempt >= this.maxAttempts) throw err;
            urls.delete(n); // the URL may have expired — re-sign
            await this.sleep(Math.min(8000, 500 * 2 ** (attempt - 1)), signal);
          }
        }
      }
    };
    await Promise.all(
      Array.from({ length: Math.min(this.concurrency, Math.max(1, todo.length)) }, worker),
    );
    if (aborted()) throw new UploadCancelled();

    this.patch(id, { phase: 'completing', sent: file.size, etaS: 0 });
    const parts = [...etags.entries()]
      .sort(([a], [b]) => a - b)
      .map(([part_number, etag]) => ({ part_number, etag }));
    await this.deps.api.completeUpload(videoId, parts);
    this.storage?.removeItem(key);

    let jobId: string | null = null;
    if (autoAnalyze) jobId = (await this.deps.api.createJob(videoId, jobConfig)).id;
    this.patch(id, { phase: 'done', jobId, bytesPerSec: 0, etaS: null });
    return { videoId, jobId };
  }
}

/** Real transport: XHR for upload progress (fetch has none); ETag needs bucket CORS `ExposeHeaders`. */
export const xhrPutPart: PutPart = (url, body, onProgress, signal) =>
  new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', url);
    xhr.upload.onprogress = (e) => onProgress(e.loaded);
    xhr.onload = () =>
      xhr.status >= 200 && xhr.status < 300
        ? resolve(xhr.getResponseHeader('ETag') ?? '')
        : reject(new Error(`Part upload failed: HTTP ${xhr.status}`));
    xhr.onerror = () => reject(new Error('Network error while uploading a part'));
    xhr.onabort = () => reject(new UploadCancelled());
    signal.addEventListener('abort', () => xhr.abort());
    xhr.send(body);
  });
