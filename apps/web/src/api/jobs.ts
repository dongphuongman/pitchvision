import { idempotencyKey, requestJson } from './pending';
import type { ServerErrorCode } from './errors';
import type { JobStatus } from '@/ui/status';

/**
 * Jobs API (backend M1 W3, final shapes; not yet in apps/api/openapi.json — move to the
 * generated client when the spec lands at merge):
 *
 *   POST /api/v1/jobs {video_id, config?}       → 202 Job  (Idempotency-Key; 409 job_id | reason)
 *   GET  /api/v1/jobs/{id}                       → Job
 *   GET  /api/v1/videos/{video_id}/jobs          → { items: Job[] } newest first
 *   POST /api/v1/jobs/{id}/cancel                → 200 Job
 *   POST /api/v1/jobs/{id}/retry                 → 202 Job  (409 if not failed / not retryable)
 *   WS   topic `job:{id}`                        → progress | completed | failed (AD §10.3)
 *
 * Video status is not changed by jobs: the library derives "analyzing / ready / failed" from the
 * latest job of each video.
 */
export type JobStage =
  | 'ingest'
  | 'normalize'
  | 'shots'
  | 'plan'
  | 'calibrate'
  | 'infer'
  | 'stitch'
  | 'tiles'
  | 'load_db';

export interface Job {
  id: string;
  video_id: string;
  status: JobStatus;
  /** 0–100. */
  progress: number;
  stage: JobStage | null;
  segments: { done: number; running: number; total: number } | null;
  fps: number | null;
  eta_s: number | null;
  error: { code: ServerErrorCode; retryable: boolean; detail?: string | null } | null;
  coverage: { main_wide_pct: number } | null;
  image_tag?: string | null;
  config?: Record<string, unknown> | null;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
}

/** Stage name (ProgressEvent.data.stage) → job status (AD §16.1, pipeline-stages.md). */
export const STAGE_STATUS: Record<JobStage, JobStatus> = {
  ingest: 'ingesting',
  normalize: 'calibrating',
  shots: 'calibrating',
  plan: 'calibrating',
  calibrate: 'calibrating',
  infer: 'inferring',
  stitch: 'postprocessing',
  tiles: 'postprocessing',
  load_db: 'postprocessing',
};

export async function getLatestJob(videoId: string, signal?: AbortSignal): Promise<Job | null> {
  const page = await requestJson<{ items: Job[] }>(
    'GET',
    `/api/v1/videos/${encodeURIComponent(videoId)}/jobs`,
    { signal },
  );
  return page.items[0] ?? null;
}

export function getJob(jobId: string, signal?: AbortSignal): Promise<Job> {
  return requestJson<Job>('GET', `/api/v1/jobs/${encodeURIComponent(jobId)}`, { signal });
}

export function retryJob(jobId: string): Promise<Job> {
  return requestJson<Job>('POST', `/api/v1/jobs/${encodeURIComponent(jobId)}/retry`, {
    headers: { 'Idempotency-Key': idempotencyKey() },
  });
}

export function cancelJob(jobId: string): Promise<Job> {
  return requestJson<Job>('POST', `/api/v1/jobs/${encodeURIComponent(jobId)}/cancel`);
}

export function canRetry(job: Pick<Job, 'status' | 'error'> | null | undefined): boolean {
  return job?.status === 'failed' && job.error?.retryable !== false;
}
