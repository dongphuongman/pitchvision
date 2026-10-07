import { keepPreviousData, queryOptions } from '@tanstack/react-query';
import { api } from './client';
import type { Heatmap, Stats } from './types';

/** `GET /results/{id}/stats` (M3, SCR-20a): team physical stats over the observed time + tracks for SCR-20c. */
export function statsQuery(videoId: string, revision?: number) {
  return queryOptions({
    queryKey: ['results', videoId, 'stats', revision ?? null] as const,
    queryFn: async ({ signal }): Promise<Stats> => {
      const { data } = await api.GET('/api/v1/results/{video_id}/stats', {
        params: { path: { video_id: videoId } },
        signal,
      });
      if (!data) throw new Error('Empty stats response');
      return data;
    },
    staleTime: 5 * 60 * 1000,
  });
}

export interface HeatmapFilter {
  cluster?: number;
  track_id?: number;
  from_ms?: number;
  to_ms?: number;
}

/** `GET /results/{id}/heatmap` (SCR-20c E01–E03): occupancy grid, 2.5 m cells, team / track / time filtered. */
export function heatmapQuery(videoId: string, filter: HeatmapFilter, revision?: number) {
  return queryOptions({
    queryKey: ['results', videoId, 'heatmap', filter, revision ?? null] as const,
    queryFn: async ({ signal }): Promise<Heatmap> => {
      const { data } = await api.GET('/api/v1/results/{video_id}/heatmap', {
        params: { path: { video_id: videoId }, query: filter },
        signal,
      });
      if (!data) throw new Error('Empty heatmap response');
      return data;
    },
    staleTime: 5 * 60 * 1000,
    placeholderData: keepPreviousData, // keep the old map while a filter change loads
  });
}
