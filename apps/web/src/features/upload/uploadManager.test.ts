/* eslint-disable @typescript-eslint/require-await, @typescript-eslint/unbound-method --
   fakes keep the async signatures of the real API; assertions read vi.fn() members of the fake api */
import { describe, expect, it, vi } from 'vitest';
import { UploadManager, resumeKey, type PutPart, type UploadApi } from './uploadManager';

const MiB = 1024 * 1024;
const PART = 16 * MiB;

function memoryStorage() {
  const m = new Map<string, string>();
  return {
    getItem: (k: string) => m.get(k) ?? null,
    setItem: (k: string, v: string) => void m.set(k, v),
    removeItem: (k: string) => void m.delete(k),
    map: m,
  };
}

/** A File of `size` bytes without allocating them (slice() is all the manager uses). */
function fakeFile(size: number, name = 'match.mp4'): File {
  const f = new File([], name, { type: 'video/mp4', lastModified: 1 });
  Object.defineProperty(f, 'size', { value: size });
  f.slice = (start = 0, end = size) =>
    new Blob([new Uint8Array(Math.max(0, Math.min(end, size) - start))]);
  return f;
}

function fakeApi(size: number, overrides: Partial<UploadApi> = {}) {
  const partCount = Math.ceil(size / PART);
  const calls: string[] = [];
  const api: UploadApi = {
    createUpload: vi.fn(async () => {
      calls.push('create');
      return {
        video_id: 'v1',
        upload_id: 'up',
        part_size: PART,
        part_count: partCount,
        parts: [{ part_number: 1, url: 'u1' }],
        url_expires_in: 900,
        video: {} as never,
      };
    }),
    signParts: vi.fn(async (_v: string, ns: number[]) => {
      calls.push(`sign:${ns.join(',')}`);
      return ns.map((n) => ({ part_number: n, url: `u${n}` }));
    }),
    getUploadState: vi.fn(async () => {
      throw new Error('no state');
    }),
    completeUpload: vi.fn(async () => {
      calls.push('complete');
      return {};
    }),
    abortUpload: vi.fn(async () => {
      calls.push('abort');
    }),
    createJob: vi.fn(async () => {
      calls.push('job');
      return { id: 'j1' };
    }),
    ...overrides,
  };
  return { api, calls, partCount };
}

const okPut: PutPart = async (url, body, onProgress) => {
  onProgress(body.size);
  return `"etag-${url}"`;
};
const noSleep: (ms: number, signal: AbortSignal) => Promise<void> = async () => undefined;

describe('UploadManager', () => {
  it('uploads every part, completes with sorted ETags and starts the job', async () => {
    const size = 3 * PART + 123;
    const { api, calls } = fakeApi(size);
    const storage = memoryStorage();
    const put = vi.fn(okPut);
    const m = new UploadManager({ api, putPart: put, storage, sleep: noSleep }, { concurrency: 3 });
    const file = fakeFile(size);
    const { id, done } = m.start(file, { title: 'T', autoAnalyze: true });
    expect(await done).toEqual({ videoId: 'v1', jobId: 'j1' });
    expect(put).toHaveBeenCalledTimes(4);
    expect(api.completeUpload).toHaveBeenCalledWith(
      'v1',
      [1, 2, 3, 4].map((n) => ({ part_number: n, etag: `"etag-u${n}"` })),
    );
    expect(calls.at(-1)).toBe('job');
    expect(storage.map.has(resumeKey(file))).toBe(false);
    expect(m.get(id)).toMatchObject({ phase: 'done', sent: size, jobId: 'j1' });
    expect(m.activeCount()).toBe(0);
  });

  it('does not create a job when "start analysis" is off', async () => {
    const { api } = fakeApi(PART);
    const m = new UploadManager({ api, putPart: okPut, storage: null, sleep: noSleep });
    expect(await m.start(fakeFile(PART), { title: null, autoAnalyze: false }).done).toEqual({
      videoId: 'v1',
      jobId: null,
    });
    expect(api.createJob).not.toHaveBeenCalled();
  });

  it('retries a failing part with a freshly signed URL', async () => {
    const { api } = fakeApi(PART);
    let attempts = 0;
    const put: PutPart = async (url, body, onProgress) => {
      attempts += 1;
      if (attempts < 3) throw new Error('network');
      onProgress(body.size);
      return `"e-${url}"`;
    };
    const sleep = vi.fn(noSleep);
    const m = new UploadManager({ api, putPart: put, storage: null, sleep });
    await m.start(fakeFile(PART), { title: null, autoAnalyze: false }).done;
    expect(attempts).toBe(3);
    expect(sleep.mock.calls.map((c) => c[0])).toEqual([500, 1000]);
    expect(api.signParts).toHaveBeenCalledTimes(2); // URL of part 1 dropped after each failure
  });

  it('fails after maxAttempts and reports the error on the item', async () => {
    const { api } = fakeApi(PART);
    const put: PutPart = async () => {
      throw new Error('boom');
    };
    const m = new UploadManager(
      { api, putPart: put, storage: null, sleep: noSleep },
      { maxAttempts: 2 },
    );
    const { id, done } = m.start(fakeFile(PART), { title: null, autoAnalyze: true });
    await expect(done).rejects.toThrow('boom');
    expect(m.get(id)).toMatchObject({ phase: 'failed' });
    expect(api.completeUpload).not.toHaveBeenCalled();
  });

  it('fails clearly when storage hides the ETag header (bucket CORS)', async () => {
    const { api } = fakeApi(PART);
    const m = new UploadManager(
      { api, putPart: async () => '', storage: null, sleep: noSleep },
      { maxAttempts: 1 },
    );
    await expect(m.start(fakeFile(PART), { title: null, autoAnalyze: false }).done).rejects.toThrow(
      /ETag/,
    );
  });

  it('resumes: re-selecting the same file uploads only the parts storage does not have', async () => {
    const size = 4 * PART;
    const storage = memoryStorage();
    const file = fakeFile(size);
    storage.setItem(resumeKey(file), 'v-old');
    const { api } = fakeApi(size, {
      getUploadState: vi.fn(async () => ({
        video_id: 'v-old',
        upload_id: 'up',
        part_size: PART,
        part_count: 4,
        parts: [
          { part_number: 1, etag: '"a"', size: PART },
          { part_number: 3, etag: '"c"', size: PART },
          { part_number: 2, etag: '"short"', size: 10 }, // truncated part → must be re-sent
        ],
      })),
    });
    const put = vi.fn(okPut);
    const m = new UploadManager({ api, putPart: put, storage, sleep: noSleep });
    const { id, done } = m.start(file, { title: null, autoAnalyze: false });
    await done;
    expect(api.createUpload).not.toHaveBeenCalled();
    expect(put.mock.calls.map((c) => c[0]).sort()).toEqual(['u2', 'u4']);
    expect(api.completeUpload).toHaveBeenCalledWith('v-old', [
      { part_number: 1, etag: '"a"' },
      { part_number: 2, etag: '"etag-u2"' },
      { part_number: 3, etag: '"c"' },
      { part_number: 4, etag: '"etag-u4"' },
    ]);
    expect(m.get(id)?.resumedParts).toBe(2);
  });

  it('starts over when the saved upload no longer exists', async () => {
    const storage = memoryStorage();
    const file = fakeFile(PART);
    storage.setItem(resumeKey(file), 'gone');
    const { api } = fakeApi(PART); // getUploadState rejects
    const m = new UploadManager({ api, putPart: okPut, storage, sleep: noSleep });
    await m.start(file, { title: null, autoAnalyze: false }).done;
    expect(api.createUpload).toHaveBeenCalledTimes(1);
  });

  it('cancel aborts in-flight parts and the multipart upload', async () => {
    const { api } = fakeApi(2 * PART);
    const storage = memoryStorage();
    const put: PutPart = (_url, _body, _p, signal) =>
      new Promise((_resolve, reject) =>
        signal.addEventListener('abort', () => reject(new Error('aborted'))),
      );
    const m = new UploadManager({ api, putPart: put, storage, sleep: noSleep });
    const file = fakeFile(2 * PART);
    const { id, done } = m.start(file, { title: null, autoAnalyze: true });
    await vi.waitFor(() => expect(m.get(id)?.phase).toBe('uploading'));
    expect(m.activeCount()).toBe(1);
    await m.cancel(id);
    await expect(done).rejects.toThrow();
    expect(m.get(id)?.phase).toBe('cancelled');
    expect(api.abortUpload).toHaveBeenCalledWith('v1');
    expect(storage.map.has(resumeKey(file))).toBe(false);
    expect(api.completeUpload).not.toHaveBeenCalled();
  });

  it('notifies subscribers with progress and ETA', async () => {
    const size = 2 * PART;
    const { api } = fakeApi(size);
    let t = 0;
    const put: PutPart = async (url, body, onProgress) => {
      t += 1000;
      onProgress(body.size);
      return `"e-${url}"`;
    };
    const m = new UploadManager(
      { api, putPart: put, storage: null, sleep: noSleep, now: () => t },
      { concurrency: 1 },
    );
    const seen: number[] = [];
    m.subscribe((items) => seen.push(items[0]?.sent ?? 0));
    await m.start(fakeFile(size), { title: null, autoAnalyze: false }).done;
    expect(seen).toContain(PART);
    expect(Math.max(...seen)).toBe(size);
  });
});
