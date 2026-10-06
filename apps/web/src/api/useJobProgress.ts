import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect } from 'react';
import { STAGE_STATUS, type JobStatus, type LiveJob } from './jobs';
import type { WsJobEvent } from './types';
import { patchLatestJobInCache, refreshVideoInCache, type Video } from './videos';
import { getWsClient, useWsTopic } from './ws';

const RUNNING = new Set<JobStatus>([
  'queued',
  'ingesting',
  'calibrating',
  'inferring',
  'postprocessing',
]);

export function isJobRunning(job: Pick<LiveJob, 'status'> | null | undefined): boolean {
  return !!job && RUNNING.has(job.status);
}

/** Pure reducer: a job snapshot + one WS job event → the next snapshot. */
export function applyJobEvent(job: LiveJob, event: WsJobEvent): LiveJob {
  switch (event.type) {
    case 'progress': {
      const stage = event.data.stage;
      return {
        ...job,
        status: (STAGE_STATUS as Partial<Record<string, JobStatus>>)[stage] ?? job.status,
        stage,
        progress: event.data.pct,
        eta_s: event.data.eta_s ?? null,
        fps: event.data.fps ?? job.fps ?? null,
        segments: event.data.segments ?? job.segments ?? null,
      };
    }
    case 'completed':
      return { ...job, status: 'done', progress: 100, eta_s: 0 };
    case 'failed':
      return { ...job, status: 'failed', eta_s: null, error: { ...event.data, detail: null } };
    case 'cancelled':
      return { ...job, status: 'cancelled', eta_s: null };
  }
}

/**
 * Live state of a video's latest job: the `latest_job` summary that came with the video list is
 * kept current by WS topic `job:{id}` (AD §10.3; the server also sends a snapshot event right after
 * subscribing). Events patch the cached video (list pages + detail), so every reader re-renders.
 * `completed` and a reconnect re-read the video once (`GET /videos/{id}`): coverage + pitch
 * calibration of the finished job, or whatever happened while offline (events aren't replayed).
 */
export function useJobProgress(
  video: Pick<Video, 'id' | 'latest_job'>,
  opts: { enabled?: boolean } = {},
) {
  const enabled = opts.enabled ?? true;
  const qc = useQueryClient();
  const job: LiveJob | null = enabled ? video.latest_job : null;
  const topic = job && isJobRunning(job) ? `job:${job.id}` : null;
  const videoId = video.id;

  const refresh = useCallback(() => {
    refreshVideoInCache(qc, videoId).catch(() => undefined); // the next list refetch catches up
  }, [qc, videoId]);

  const onEvent = useCallback(
    (event: WsJobEvent) => {
      const jobId = event.topic.slice('job:'.length);
      patchLatestJobInCache(qc, videoId, (prev) =>
        prev && prev.id === jobId ? applyJobEvent(prev, event) : prev,
      );
      if (event.type === 'completed') refresh();
    },
    [qc, videoId, refresh],
  );

  useWsTopic(topic, onEvent);

  useEffect(() => {
    if (!topic) return;
    return getWsClient().onStatus((status, prev) => {
      if (status === 'open' && prev === 'reconnecting') refresh();
    });
  }, [topic, refresh]);

  return { job };
}
