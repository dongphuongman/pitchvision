import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect } from 'react';
import { STAGE_STATUS, type Job, type JobStage } from './jobs';
import type { WsServerEvent } from './types';
import { latestJobQuery, videoKeys } from './videos';
import { getWsClient, useWsTopic } from './ws';

const RUNNING = new Set<Job['status']>([
  'queued',
  'ingesting',
  'calibrating',
  'inferring',
  'postprocessing',
]);

export function isJobRunning(job: Pick<Job, 'status'> | null | undefined): boolean {
  return !!job && RUNNING.has(job.status);
}

/** Pure reducer: a job snapshot + one WS event → the next snapshot. */
export function applyJobEvent(job: Job, event: WsServerEvent): Job {
  switch (event.type) {
    case 'progress': {
      const stage = event.data.stage as JobStage;
      return {
        ...job,
        status: (STAGE_STATUS as Partial<Record<string, Job['status']>>)[stage] ?? job.status,
        stage,
        progress: event.data.pct,
        eta_s: event.data.eta_s ?? null,
        fps: event.data.fps ?? job.fps,
        segments: event.data.segments ?? job.segments,
      };
    }
    case 'completed':
      return { ...job, status: 'done', progress: 100, eta_s: 0 };
    case 'failed':
      return { ...job, status: 'failed', error: { ...event.data, detail: null } };
  }
}

/**
 * Live progress of a video's latest job: REST snapshot (`GET /videos/{id}/jobs`) kept current by
 * WS topic `job:{id}` (AD §10.3; the server also sends a snapshot event right after subscribing).
 * Events update the TanStack cache key `latestJob(videoId)`, so every reader re-renders. After a
 * reconnect the job is refetched — events sent while offline are not replayed.
 */
export function useJobProgress(videoId: string, opts: { enabled?: boolean } = {}) {
  const enabled = opts.enabled ?? true;
  const qc = useQueryClient();
  const query = useQuery({ ...latestJobQuery(videoId), enabled });
  const job = query.data ?? null;
  const topic = enabled && job && isJobRunning(job) ? `job:${job.id}` : null;
  const { refetch } = query;

  const onEvent = useCallback(
    (event: WsServerEvent) => {
      qc.setQueryData<Job | null>(videoKeys.latestJob(videoId), (prev) =>
        prev ? applyJobEvent(prev, event) : prev,
      );
      // `completed` doesn't carry coverage: fetch the finished job once.
      if (event.type === 'completed') void refetch();
    },
    [qc, videoId, refetch],
  );

  useWsTopic(topic, onEvent);

  useEffect(() => {
    if (!topic) return;
    return getWsClient().onStatus((status, prev) => {
      if (status === 'open' && prev === 'reconnecting') void refetch();
    });
  }, [topic, refetch]);

  return { job, isLoading: query.isLoading, error: query.error };
}
