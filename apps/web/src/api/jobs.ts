import { api } from './client';
import type { components } from './generated/openapi';

/**
 * Jobs API (apps/api/openapi.json, generated client):
 *
 *   POST /api/v1/jobs {video_id, config?}       → 202 JobOut  (Idempotency-Key; 409 job_id | reason)
 *   GET  /api/v1/jobs/{id}                       → JobOut
 *   GET  /api/v1/videos/{video_id}/jobs          → { items: JobOut[] } newest first
 *   POST /api/v1/jobs/{id}/cancel                → 200 JobOut
 *   POST /api/v1/jobs/{id}/retry                 → 202 JobOut  (409 if not failed / not retryable)
 *   WS   topic `job:{id}`                        → progress | completed | failed | cancelled (AD §10.3)
 *
 * Lists don't fetch jobs: every `VideoOut` carries `latest_job` (a `LatestJob` summary) and the
 * library keeps it live over the WebSocket.
 */
export type Job = components['schemas']['JobOut'];
export type LatestJob = components['schemas']['LatestJob'];
export type JobStatus = components['schemas']['JobStatus'];
export type PitchCalibration = NonNullable<LatestJob['pitch_calibration']>;

/** `LatestJob` plus the live-only fields WS progress events carry (absent from list responses). */
export type LiveJob = LatestJob & Partial<Pick<Job, 'eta_s' | 'fps' | 'segments'>>;

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

/**
 * A full job (e.g. the retry response) as the `latest_job` summary of its video. JobOut has no
 * pitch calibration: keep the one the cache already knows (same job) or null.
 */
export function toLatestJob(job: Job, previous?: LatestJob | null): LiveJob {
  return {
    id: job.id,
    status: job.status,
    progress: job.progress,
    stage: job.stage ?? null,
    error: job.error ?? null,
    coverage: job.coverage ?? null,
    pitch_calibration: previous?.id === job.id ? previous.pitch_calibration : null,
    eta_s: job.eta_s ?? null,
    fps: job.fps ?? null,
    segments: job.segments ?? null,
  };
}

function required<T>(data: T | undefined, what: string): T {
  if (data === undefined) throw new Error(`Empty ${what} response`);
  return data;
}

export async function getJob(jobId: string, signal?: AbortSignal): Promise<Job> {
  const { data } = await api.GET('/api/v1/jobs/{job_id}', {
    params: { path: { job_id: jobId } },
    signal,
  });
  return required(data, 'job');
}

export async function retryJob(jobId: string): Promise<Job> {
  const { data } = await api.POST('/api/v1/jobs/{job_id}/retry', {
    params: { path: { job_id: jobId } },
  });
  return required(data, 'retry');
}

export async function cancelJob(jobId: string): Promise<Job> {
  const { data } = await api.POST('/api/v1/jobs/{job_id}/cancel', {
    params: { path: { job_id: jobId } },
  });
  return required(data, 'cancel');
}

export function canRetry(job: Pick<LatestJob, 'status' | 'error'> | null | undefined): boolean {
  return job?.status === 'failed' && job.error?.retryable !== false;
}
