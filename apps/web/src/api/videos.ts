import {
  infiniteQueryOptions,
  queryOptions,
  type InfiniteData,
  type QueryClient,
} from '@tanstack/react-query';
import { api } from './client';
import type { components } from './generated/openapi';
import { getLatestJob } from './jobs';
import { requestJson } from './pending';
import type { Manifest } from './types';

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
  manifest: (id: string) => ['videos', 'manifest', id] as const,
  latestJob: (id: string) => ['videos', 'latest-job', id] as const,
};

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

export function videoQuery(id: string) {
  return queryOptions({
    queryKey: videoKeys.detail(id),
    queryFn: async ({ signal }) => {
      const { data } = await api.GET('/api/v1/videos/{video_id}', {
        params: { path: { video_id: id } },
        signal,
      });
      if (!data) throw new Error('Empty video response');
      return data;
    },
  });
}

export function latestJobQuery(videoId: string) {
  return queryOptions({
    queryKey: videoKeys.latestJob(videoId),
    queryFn: ({ signal }) => getLatestJob(videoId, signal),
    staleTime: 30_000,
  });
}

/**
 * Manifest of the latest finished analysis. ASSUMED path (AD §10.2 `GET /results/{id}/manifest`,
 * keyed by video id here); not yet in openapi.json.
 */
export function manifestQuery(videoId: string) {
  return queryOptions({
    queryKey: videoKeys.manifest(videoId),
    queryFn: ({ signal }) =>
      requestJson<Manifest>('GET', `/api/v1/results/${encodeURIComponent(videoId)}/manifest`, {
        signal,
      }),
    staleTime: 5 * 60_000,
  });
}

/** ASSUMED: `PATCH /api/v1/videos/{id}` `{ title }` → VideoOut. */
export function renameVideo(id: string, title: string): Promise<Video> {
  return requestJson<Video>('PATCH', `/api/v1/videos/${encodeURIComponent(id)}`, {
    body: { title },
  });
}

/** ASSUMED: `DELETE /api/v1/videos/{id}` → 204 (cancels a running job server-side first). */
export function deleteVideo(id: string): Promise<void> {
  return requestJson<undefined>('DELETE', `/api/v1/videos/${encodeURIComponent(id)}`);
}

/* ------------------------------------------------------------------ cache helpers */

/** Patch one video wherever it is cached (list pages + detail). */
export function patchVideoInCache(qc: QueryClient, id: string, patch: Partial<Video>): void {
  qc.setQueryData<InfiniteData<VideoPage, string | null>>(videoKeys.list(), (data) =>
    data
      ? {
          ...data,
          pages: data.pages.map((p) => ({
            ...p,
            items: p.items.map((v) => (v.id === id ? { ...v, ...patch } : v)),
          })),
        }
      : data,
  );
  qc.setQueryData<Video>(videoKeys.detail(id), (v) => (v ? { ...v, ...patch } : v));
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
