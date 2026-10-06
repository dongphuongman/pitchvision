import type { Job } from '@/api/jobs';
import type { Video } from '@/api/videos';
import type { VideoStatus } from '@/ui';

/* SCR-10 Library: pure view-model helpers (status mapping, filters, sort). */

export const STATUS_FILTERS = ['all', 'ready', 'analyzing', 'needs_action', 'failed'] as const;
export type StatusFilter = (typeof STATUS_FILTERS)[number];
/** Live recording is Phase C (hidden, UIUX v1.1). */
export const SOURCE_FILTERS = ['all', 'upload', 'youtube'] as const;
export type SourceFilter = (typeof SOURCE_FILTERS)[number];
export const SORTS = ['newest', 'oldest', 'title', 'duration'] as const;
export type LibrarySort = (typeof SORTS)[number];

export interface LibraryFilters {
  q?: string;
  status?: StatusFilter;
  source?: SourceFilter;
  sort?: LibrarySort;
}

/**
 * Card badge (UIUX SCR-10 E10) from `VideoOut.status` + the video's latest job. Jobs don't change
 * the video status: while the source is `ready`, the job tells "queued / analyzing / ready /
 * failed / cancelled"; no job yet = "Not analyzed". `job === undefined` means "not loaded yet".
 * `processing` / `done` video statuses are honoured too in case the API starts setting them.
 */
export function cardStatus(
  video: Pick<Video, 'status'>,
  job?: Pick<Job, 'status'> | null,
): VideoStatus {
  switch (video.status) {
    case 'uploading':
      return 'uploading';
    case 'ingesting':
      return 'ingesting';
    case 'failed':
      return 'failed';
    case 'deleted':
      return 'cancelled';
    default:
      break;
  }
  switch (job?.status) {
    case 'queued':
      return 'queued';
    case 'ingesting':
    case 'calibrating':
    case 'inferring':
    case 'postprocessing':
      return 'analyzing';
    case 'done':
      return 'ready';
    case 'failed':
      return 'failed';
    case 'cancelled':
      return 'cancelled';
    default:
      return video.status === 'done'
        ? 'ready'
        : video.status === 'processing'
          ? 'analyzing'
          : 'not_analyzed';
  }
}

/** Videos whose latest job matters (source available). */
export function hasJobs(video: Pick<Video, 'status'>): boolean {
  return video.status === 'ready' || video.status === 'processing' || video.status === 'done';
}

const STATUS_GROUP: Record<VideoStatus, StatusFilter> = {
  uploading: 'analyzing',
  ingesting: 'analyzing',
  queued: 'analyzing',
  analyzing: 'analyzing',
  not_analyzed: 'all',
  needs_team_confirmation: 'needs_action',
  needs_calibration: 'needs_action',
  ready: 'ready',
  ready_degraded: 'ready',
  failed: 'failed',
  cancelled: 'all',
};

export function matchesStatus(status: VideoStatus, filter: StatusFilter): boolean {
  return filter === 'all' || STATUS_GROUP[status] === filter;
}

export function normalizeSearch(s: string): string {
  return s
    .normalize('NFD')
    .replace(/\p{Diacritic}/gu, '')
    .toLowerCase()
    .trim();
}

/**
 * Client-side search / filter / sort over the pages loaded so far (server-side filtering lands
 * with the list endpoint's query params). Titles only — team names come with M2 rosters.
 */
export function applyLibraryFilters(
  videos: readonly Video[],
  filters: LibraryFilters,
  statusOf: (v: Video) => VideoStatus = (v) => cardStatus(v),
): Video[] {
  const q = normalizeSearch(filters.q ?? '');
  const status = filters.status ?? 'all';
  const source = filters.source ?? 'all';
  const out = videos.filter(
    (v) =>
      v.status !== 'deleted' &&
      (q === '' || normalizeSearch(v.title ?? '').includes(q)) &&
      (source === 'all' || v.source_type === source) &&
      matchesStatus(statusOf(v), status),
  );
  const sort = filters.sort ?? 'newest';
  const byDate = (a: Video, b: Video) => a.created_at.localeCompare(b.created_at);
  switch (sort) {
    case 'newest':
      return out.sort((a, b) => byDate(b, a));
    case 'oldest':
      return out.sort(byDate);
    case 'title':
      return out.sort((a, b) =>
        (a.title ?? '').localeCompare(b.title ?? '', undefined, { sensitivity: 'base' }),
      );
    case 'duration':
      return out.sort((a, b) => (b.duration_ms ?? -1) - (a.duration_ms ?? -1));
  }
}

export function hasActiveFilters(f: LibraryFilters): boolean {
  return !!f.q || (f.status ?? 'all') !== 'all' || (f.source ?? 'all') !== 'all';
}

/** Where a card leads (UIUX SCR-10 E10). `null` = not navigable (upload popover). */
export type CardTarget =
  { kind: 'processing'; videoId: string } | { kind: 'workspace'; videoId: string } | null;

export function cardTarget(videoId: string, status: VideoStatus): CardTarget {
  switch (status) {
    case 'uploading':
      return null;
    case 'ready':
    case 'ready_degraded':
      return { kind: 'workspace', videoId };
    default:
      // preparing / queued / analyzing / failed / cancelled → SCR-13; "not analyzed" also lands
      // there until SCR-12 (re-analyze) exists.
      return { kind: 'processing', videoId };
  }
}

export const TITLE_MAX = 200;

export function videoTitle(video: Pick<Video, 'title'>, fallback: string): string {
  return video.title?.trim() || fallback;
}
