import { queryOptions } from '@tanstack/react-query';
import { api } from './client';
import type { Manifest } from './types';

/** `GET /results/{id}/manifest`: shots, coverage, teams, pitch status, signed tile URL template. */
export function manifestQuery(videoId: string) {
  return queryOptions({
    queryKey: ['results', videoId, 'manifest'] as const,
    queryFn: async ({ signal }): Promise<Manifest> => {
      const { data } = await api.GET('/api/v1/results/{video_id}/manifest', {
        params: { path: { video_id: videoId } },
        signal,
      });
      if (!data) throw new Error('Empty manifest response');
      return data;
    },
    // Tile URLs are signed for 6 h: refresh well before, and on any tile 403 (TileStore error).
    staleTime: 60 * 60 * 1000,
    refetchInterval: 4 * 60 * 60 * 1000,
  });
}

export interface Media {
  video_url: string;
  expires_in: number;
  fps: number;
  width: number;
  height: number;
}

/** `GET /results/{id}/media`: presigned URL of normalized.mp4 (25 fps, 1280) for the player. */
export function mediaQuery(videoId: string) {
  return queryOptions({
    queryKey: ['results', videoId, 'media'] as const,
    queryFn: async ({ signal }): Promise<Media> => {
      const { data } = await api.GET('/api/v1/results/{video_id}/media', {
        params: { path: { video_id: videoId } },
        signal,
      });
      if (!data) throw new Error('Empty media response');
      return data;
    },
    staleTime: 60 * 60 * 1000,
  });
}
