import {
  infiniteQueryOptions,
  queryOptions,
  type InfiniteData,
  type QueryClient,
} from '@tanstack/react-query';
import { api } from './client';
import type { components } from './generated/openapi';
import type { LiveJob } from './jobs';

export type Video = components['schemas']['VideoOut'];
export type VideoPage = components['schemas']['VideoPage'];
/** Video status as stored by the API (`VideoOut.status`). */
export type ApiVideoStatus = Video['status'];
export type VideoSource = NonNullable<Video['source_type']>;

export const LIBRARY_PAGE_SIZE = 20;

/** Query keys for videos; WS handlers patch the same keys with setQueryData. */
export const videoKeys = {
  all: ['videos'] as const,
  list: () => ['videos', 'list'] as const,
  detail: (id: string) => ['videos', 'detail', id] as const,
};

/** Every item carries `latest_job` (same query server-side): no per-video job requests. */
export const videosInfiniteQuery = infiniteQueryOptions({
  queryKey: videoKeys.list(),
  queryFn: async ({ pageParam, signal }): Promise<VideoPage> => {
    const { data } = await api.GET('/api/v1/videos', {
      params: { query: { cursor: pageParam ?? undefined, limit: LIBRARY_PAGE_SIZE } },
      signal,
    });
    if (!data) throw new Error('Empty /api/v1/videos response');
    return data;
  },
  initialPageParam: null as string | null,
  getNextPageParam: (last) => last.next_cursor ?? undefined,
});

export async function getVideo(id: string, signal?: AbortSignal): Promise<Video> {
  const { data } = await api.GET('/api/v1/videos/{video_id}', {
    params: { path: { video_id: id } },
    signal,
  });
  if (!data) throw new Error('Empty video response');
  return data;
}

export function videoQuery(id: string) {
  return queryOptions({
    queryKey: videoKeys.detail(id),
    queryFn: ({ signal }) => getVideo(id, signal),
  });
}

/** `PATCH /api/v1/videos/{id}` `{ title }` (editor+; 1–200 chars after trimming) → VideoOut. */
export async function renameVideo(id: string, title: string): Promise<Video> {
  const { data } = await api.PATCH('/api/v1/videos/{video_id}', {
    params: { path: { video_id: id } },
    body: { title },
  });
  if (!data) throw new Error('Empty rename response');
  return data;
}

/**
 * `DELETE /api/v1/videos/{id}` → 204. The server cancels a running job (WS `cancelled`), aborts an
 * unfinished upload and erases every stored object in the background.
 */
export async function deleteVideo(id: string): Promise<void> {
  await api.DELETE('/api/v1/videos/{video_id}', { params: { path: { video_id: id } } });
}

/* ------------------------------------------------------------------ cache helpers */

/** Patch one video wherever it is cached (list pages + detail). */
export function patchVideoInCache(qc: QueryClient, id: string, patch: Partial<Video>): void {
  updateVideoInCache(qc, id, (v) => ({ ...v, ...patch }));
}

/** Apply `fn` to the video's `latest_job` wherever it is cached (WS events, retry). */
export function patchLatestJobInCache(
  qc: QueryClient,
  id: string,
  fn: (job: LiveJob | null) => LiveJob | null,
): void {
  updateVideoInCache(qc, id, (v) => {
    const next = fn(v.latest_job);
    return next === v.latest_job ? v : { ...v, latest_job: next };
  });
}

function updateVideoInCache(qc: QueryClient, id: string, fn: (v: Video) => Video): void {
  qc.setQueryData<InfiniteData<VideoPage, string | null>>(videoKeys.list(), (data) =>
    data
      ? {
          ...data,
          pages: data.pages.map((p) => ({
            ...p,
            items: p.items.map((v) => (v.id === id ? fn(v) : v)),
          })),
        }
      : data,
  );
  qc.setQueryData<Video>(videoKeys.detail(id), (v) => (v ? fn(v) : v));
}

/** Re-read one video (e.g. its finished job's coverage / pitch calibration) into the caches. */
export async function refreshVideoInCache(qc: QueryClient, id: string): Promise<void> {
  const fresh = await getVideo(id);
  patchVideoInCache(qc, id, fresh);
}

export function removeVideoFromCache(qc: QueryClient, id: string): void {
  qc.setQueryData<InfiniteData<VideoPage, string | null>>(videoKeys.list(), (data) =>
    data
      ? {
          ...data,
          pages: data.pages.map((p) => ({ ...p, items: p.items.filter((v) => v.id !== id) })),
        }
      : data,
  );
  qc.removeQueries({ queryKey: videoKeys.detail(id) });
}

export function findVideoInCache(qc: QueryClient, id: string): Video | undefined {
  const data = qc.getQueryData<InfiniteData<VideoPage, string | null>>(videoKeys.list());
  for (const p of data?.pages ?? []) {
    const v = p.items.find((x) => x.id === id);
    if (v) return v;
  }
  return qc.getQueryData<Video>(videoKeys.detail(id));
}
