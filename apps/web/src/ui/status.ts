import type { BadgeTone } from './classes';

/* ---------- Domain status → tone mappings (labels come from i18n at the call site) ---------- */

/** Pipeline job status (packages/schemas JobStatus). */
export type JobStatus =
  | 'queued'
  | 'ingesting'
  | 'calibrating'
  | 'inferring'
  | 'postprocessing'
  | 'done'
  | 'failed'
  | 'cancelled';

export const JOB_STATUS_TONE: Record<JobStatus, BadgeTone> = {
  queued: 'mute',
  ingesting: 'run',
  calibrating: 'run',
  inferring: 'run',
  postprocessing: 'run',
  done: 'ok',
  failed: 'err',
  cancelled: 'mute',
};

/** Video status as shown on library cards (UIUX SCR-10 E10). */
export type VideoStatus =
  | 'uploading'
  | 'queued'
  | 'analyzing'
  | 'needs_team_confirmation'
  | 'needs_calibration'
  | 'ready'
  | 'ready_degraded'
  | 'failed'
  | 'cancelled';

export const VIDEO_STATUS_TONE: Record<VideoStatus, BadgeTone> = {
  uploading: 'run',
  queued: 'run',
  analyzing: 'run',
  needs_team_confirmation: 'warn',
  needs_calibration: 'warn',
  ready: 'ok',
  ready_degraded: 'ok',
  failed: 'err',
  cancelled: 'mute',
};

/** Broadcast shot type (packages/schemas ShotType). Only main_wide carries tactical data. */
export type ShotType = 'main_wide' | 'close_up' | 'replay' | 'other';

export const SHOT_TYPE_TONE: Record<ShotType, BadgeTone> = {
  main_wide: 'ok',
  close_up: 'mute',
  replay: 'mute',
  other: 'mute',
};
