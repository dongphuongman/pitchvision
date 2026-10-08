import { queryOptions, type QueryClient } from '@tanstack/react-query';
import { api } from './client';
import { isApiError } from './errors';
import type { components } from './generated/openapi';
import { toLatestJob, type Job } from './jobs';
import { patchVideoInCache } from './videos';

/**
 * User corrections of an analysis (apps/api/openapi.json, generated client):
 *
 *   GET   /results/{id}/teams              → TeamList (names, colours, sample sprite sheet)
 *   PATCH /results/{id}/teams              → TeamList              SCR-14 E09 + E05 attack_h1 (409 no_teams)
 *   GET   /results/{id}/players            → PlayerList            SCR-14 E06 goalkeepers / referees
 *   PATCH /results/{id}/players            → PlayerList            SCR-14 E06 fix (422 reason)
 *   GET   /results/{id}/teams/candidates   → TeamCandidates        SCR-14 manual (404 older jobs)
 *   POST  /results/{id}/teams/manual       → 202 JobOut team_rerun (Idempotency-Key; 409)
 *   GET   /results/{id}/calibration        → CalibrationState      SCR-15
 *   POST  /results/{id}/calibration        → 202 CalibrationAccepted (Idempotency-Key; 422 reason)
 *   GET   /results/{id}/calibration/frame  → still JPEG of a frame (503 = extractor busy, retried)
 *   GET   /results/pitch-template          → PitchTemplateInfo     32 landmarks + painted lines
 */
export type TeamList = components['schemas']['TeamList'];
export type Team = components['schemas']['TeamOut'];
export type TeamEdit = components['schemas']['TeamEdit'];
export type AttackSide = 'left' | 'right';
export type PlayerList = components['schemas']['PlayerList'];
export type PlayerOut = components['schemas']['PlayerOut'];
export type PlayerFix = components['schemas']['PlayerFix'];
export type SampleCrop = components['schemas']['SampleCrop'];
export type SampleSheet = components['schemas']['SampleSheet'];
export type TeamCandidates = components['schemas']['TeamCandidates'];
export type CandidateFrame = components['schemas']['CandidateFrame'];
export type SeedBox = components['schemas']['SeedBox'];
export type CalibrationState = components['schemas']['CalibrationState'];
export type CalibrationPoint = components['schemas']['CalibrationPoint'];
export type CalibrationScope = components['schemas']['CalibrationIn']['scope'];
export type CalibrationAccepted = components['schemas']['CalibrationAccepted'];
export type ManualCalibration = components['schemas']['ManualCalibration'];
export type PitchTemplate = components['schemas']['PitchTemplateInfo'];
export type TemplateKeypoint = components['schemas']['TemplateKeypoint'];

/** SCR-22 track corrections (M2-BE-01/02): merge / split / remove + Undo. */
export type CorrectionsState = components['schemas']['CorrectionsState'];
export type CorrectionOut = components['schemas']['CorrectionOut'];
export type MergeIn = components['schemas']['MergeIn'];
export type SplitIn = components['schemas']['SplitIn'];
export type RemoveIn = components['schemas']['RemoveIn'];
export type CorrectionIn = MergeIn | SplitIn | RemoveIn;

export interface Frame {
  url: string;
  t_ms: number;
  expires_in: number;
}

/** 422 `reason` values of POST /calibration (shown inline on SCR-15). */
export const CALIBRATION_REASONS = [
  'frame_ms',
  'duplicate_landmark',
  'collinear',
  'not_analysed_shot',
] as const;
export type CalibrationReason = (typeof CALIBRATION_REASONS)[number];

/** Normalized-video frame grid (25 fps) the frame endpoint snaps to. */
export const FRAME_MS = 40;

export function snapMs(ms: number): number {
  return Math.max(0, Math.round(ms / FRAME_MS) * FRAME_MS);
}

type Box = [number, number, number, number];

/**
 * openapi-fetch widens tuple arrays in response bodies to `number[][]`: narrow them back to the
 * contract's tuples (malformed entries are dropped rather than trusted).
 */
function toBox(b: readonly number[]): Box | null {
  const [x1, y1, x2, y2] = b;
  return x1 !== undefined && y1 !== undefined && x2 !== undefined && y2 !== undefined
    ? [x1, y1, x2, y2]
    : null;
}

function toXY(p: readonly number[]): [number, number] | null {
  const [x, y] = p;
  return x !== undefined && y !== undefined ? [x, y] : null;
}

function required<T>(data: T | undefined, what: string): T {
  if (data === undefined) throw new Error(`Empty ${what} response`);
  return data;
}

/** `reason` extension member of a problem+json error, if any. */
export function problemReason(error: unknown): string | undefined {
  if (!isApiError(error)) return undefined;
  const r = error.problem.reason;
  return typeof r === 'string' ? r : undefined;
}

/** `job_id` extension member of a 409 (a job is already running for the video). */
export function problemJobId(error: unknown): string | undefined {
  if (!isApiError(error)) return undefined;
  const id = error.problem.job_id;
  return typeof id === 'string' ? id : undefined;
}

export function teamsQuery(videoId: string) {
  return queryOptions({
    queryKey: ['results', videoId, 'teams'] as const,
    queryFn: async ({ signal }): Promise<TeamList> => {
      const { data } = await api.GET('/api/v1/results/{video_id}/teams', {
        params: { path: { video_id: videoId } },
        signal,
      });
      return required(data, 'teams');
    },
    // The sample sheet URL is presigned: refetch well before it expires.
    staleTime: 5 * 60 * 1000,
  });
}

/**
 * Sends the final mapping (`swap: false`, as the API recommends) and confirms the teams.
 * `attackH1`: side team cluster 0 attacks in the first half (E05; cluster 1 gets the other).
 */
export async function patchTeams(
  videoId: string,
  teams: TeamEdit[],
  attackH1?: AttackSide | null,
): Promise<TeamList> {
  const { data } = await api.PATCH('/api/v1/results/{video_id}/teams', {
    params: { path: { video_id: videoId } },
    body: { teams, swap: false, confirmed: true, attack_h1: attackH1 ?? null },
  });
  return required(data, 'teams');
}

/** SCR-14 E06: goalkeepers + referees, longest on screen first. */
export function keyRolesQuery(videoId: string) {
  return queryOptions({
    queryKey: ['results', videoId, 'players', 'key-roles'] as const,
    queryFn: async ({ signal }): Promise<PlayerList> => {
      const { data } = await api.GET('/api/v1/results/{video_id}/players', {
        params: {
          path: { video_id: videoId },
          query: { role: ['goalkeeper', 'referee'], limit: 50 },
        },
        signal,
      });
      return required(data, 'players');
    },
  });
}

/** SCR-21 Assign + overlay labels: all tracks with names / jerseys. */
export function playersQuery(videoId: string) {
  return queryOptions({
    queryKey: ['results', videoId, 'players', 'all'] as const,
    queryFn: async ({ signal }): Promise<PlayerList> => {
      const { data } = await api.GET('/api/v1/results/{video_id}/players', {
        params: { path: { video_id: videoId }, query: { limit: 2000 } },
        signal,
      });
      return required(data, 'players');
    },
  });
}

/** E06 "Fix assignment" (display-only; refetch the manifest afterwards). */
export async function patchPlayers(videoId: string, changes: PlayerFix[]): Promise<PlayerList> {
  const { data } = await api.PATCH('/api/v1/results/{video_id}/players', {
    params: { path: { video_id: videoId } },
    body: { changes },
  });
  return required(data, 'players');
}

export function teamCandidatesQuery(videoId: string) {
  return queryOptions({
    queryKey: ['results', videoId, 'team-candidates'] as const,
    queryFn: async ({ signal }): Promise<TeamCandidates> => {
      const { data } = await api.GET('/api/v1/results/{video_id}/teams/candidates', {
        params: { path: { video_id: videoId } },
        signal,
      });
      const res = required(data, 'team candidates');
      return {
        ...res,
        frames: res.frames.map((f) => ({
          ...f,
          boxes: f.boxes.map(toBox).filter((b): b is Box => b !== null),
        })),
      };
    },
    staleTime: Infinity,
  });
}

/** SCR-20 "Map the pitch": a repitch job (automatic pitch model, detections reused). */
export async function postRepitch(
  videoId: string,
  idempotencyKey: string = crypto.randomUUID(),
): Promise<Job> {
  const { data } = await api.POST('/api/v1/results/{video_id}/repitch', {
    params: { path: { video_id: videoId } },
    headers: { 'Idempotency-Key': idempotencyKey },
  });
  return required(data, 'repitch');
}

export async function postManualTeams(
  videoId: string,
  samples: { '0': SeedBox[]; '1': SeedBox[] },
  idempotencyKey: string = crypto.randomUUID(),
): Promise<Job> {
  const { data } = await api.POST('/api/v1/results/{video_id}/teams/manual', {
    params: { path: { video_id: videoId } },
    headers: { 'Idempotency-Key': idempotencyKey },
    body: { samples },
  });
  return required(data, 'manual teams');
}

export function calibrationQuery(videoId: string) {
  return queryOptions({
    queryKey: ['results', videoId, 'calibration'] as const,
    queryFn: async ({ signal }): Promise<CalibrationState> => {
      const { data } = await api.GET('/api/v1/results/{video_id}/calibration', {
        params: { path: { video_id: videoId } },
        signal,
      });
      return required(data, 'calibration');
    },
  });
}

export async function postCalibration(
  videoId: string,
  points: CalibrationPoint[],
  scope: CalibrationScope,
  idempotencyKey: string = crypto.randomUUID(),
): Promise<CalibrationAccepted> {
  const { data } = await api.POST('/api/v1/results/{video_id}/calibration', {
    params: { path: { video_id: videoId } },
    headers: { 'Idempotency-Key': idempotencyKey },
    body: { points, scope },
  });
  return required(data, 'calibration');
}

/** Retries only "extractor busy" (503) with backoff; 404 no_video / 422 out_of_range are final. */
export function frameRetry(failureCount: number, error: unknown): boolean {
  return isApiError(error) && error.status === 503 && failureCount < 4;
}

/** Still frame of normalized.mp4 at `tMs` (snapped to 25 fps) — SCR-15 and SCR-14 manual mode. */
export function frameQuery(videoId: string, tMs: number, opts: { enabled?: boolean } = {}) {
  const t = snapMs(tMs);
  return queryOptions({
    queryKey: ['results', videoId, 'frame', t] as const,
    queryFn: async ({ signal }): Promise<Frame> => {
      const { data } = await api.GET('/api/v1/results/{video_id}/calibration/frame', {
        params: { path: { video_id: videoId }, query: { t_ms: t } },
        signal,
      });
      return required(data, 'frame');
    },
    enabled: opts.enabled ?? true,
    retry: frameRetry,
    retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 8000),
    // Presigned URL of an immutable still: keep it while the URL is valid.
    staleTime: 10 * 60 * 1000,
  });
}

export function pitchTemplateQuery() {
  return queryOptions({
    queryKey: ['results', 'pitch-template'] as const,
    queryFn: async ({ signal }): Promise<PitchTemplate> => {
      const { data } = await api.GET('/api/v1/results/pitch-template', { signal });
      const res = required(data, 'pitch template');
      return {
        ...res,
        lines: res.lines.map((l) => ({
          ...l,
          points: l.points.map(toXY).filter((p): p is [number, number] => p !== null),
        })),
      };
    },
    staleTime: Infinity,
  });
}

/** A derived job (team_rerun / recalibrate) started: SCR-13 follows it as the video's latest job. */
export function adoptJob(qc: QueryClient, videoId: string, job: Job): void {
  patchVideoInCache(qc, videoId, { latest_job: toLatestJob(job) });
}

/** SCR-22 live corrections (oldest first). Polls while an edit is pending. */
export function correctionsQuery(videoId: string) {
  return queryOptions({
    queryKey: ['results', videoId, 'corrections'] as const,
    queryFn: async ({ signal }): Promise<CorrectionsState> => {
      const { data } = await api.GET('/api/v1/results/{video_id}/corrections', {
        params: { path: { video_id: videoId } },
        signal,
      });
      return required(data, 'corrections');
    },
    // While the debounced edit job is queued/running the badge counts down to fresh results.
    refetchInterval: (query) => {
      const s = query.state.data;
      return s && (s.pending || s.job) ? 5000 : false;
    },
  });
}

/** SCR-22 E03–E05: record a merge / split / remove (202; one debounced `edit` job per burst). */
export async function postCorrection(
  videoId: string,
  body: CorrectionIn,
  idempotencyKey: string = crypto.randomUUID(),
): Promise<CorrectionsState> {
  const { data } = await api.POST('/api/v1/results/{video_id}/corrections', {
    params: { path: { video_id: videoId } },
    headers: { 'Idempotency-Key': idempotencyKey },
    body,
  });
  // The 202 goes through the idempotency envelope, so the OpenAPI schema is untyped: the body is the state.
  return required(data, 'correction') as CorrectionsState;
}

/** SCR-22 Undo toast: the correction stops applying; results follow with the next edit job. */
export async function undoCorrection(
  videoId: string,
  correctionId: string,
): Promise<CorrectionsState> {
  const { data } = await api.DELETE('/api/v1/results/{video_id}/corrections/{correction_id}', {
    params: { path: { video_id: videoId, correction_id: correctionId } },
  });
  return required(data, 'undo correction');
}

/** Start the edit job for pending corrections (after the job that blocked them finished). */
export async function applyCorrections(videoId: string): Promise<CorrectionsState> {
  const { data } = await api.POST('/api/v1/results/{video_id}/corrections/apply', {
    params: { path: { video_id: videoId } },
  });
  return required(data, 'apply corrections');
}
