import { http, HttpResponse, ws, type WebSocketHandler } from 'msw';
import { toLatestJob, type Job, type LatestJob, type PitchCalibration } from '@/api/jobs';
import type { WsControlMessage, WsJobEvent, WsServerEvent } from '@/api/types';
import type { Video } from '@/api/videos';

/**
 * In-memory videos + jobs API and the `/api/v1/ws` socket (AD §10.3), shaped like
 * apps/api/openapi.json and packages/schemas `ws_server_event`. Shared by Vitest (msw/node) and
 * the dev server (msw/browser).
 */

type WsClient = Parameters<
  Parameters<ReturnType<typeof ws.link>['addEventListener']>[1]
>[0]['client'];

/** A JobOut plus the pitch calibration the orchestrator stores for `latest_job` (W4). */
export type MockJob = Job & { pitch_calibration?: PitchCalibration | null };

export interface MockVideoDb {
  /** Any order; the list endpoint sorts newest first. `latest_job` is derived from `jobs`. */
  videos: Video[];
  jobs: MockJob[];
  /** Request log for assertions, e.g. "DELETE /api/v1/videos/v-1". */
  log: string[];
  /** Live sockets → subscribed topics. */
  sockets: Map<WsClient, Set<string>>;
}

export function createVideoDb(partial: Partial<MockVideoDb> = {}): MockVideoDb {
  return { videos: [], jobs: [], log: [], sockets: new Map(), ...partial };
}

let seq = 0;
export function mockVideo(partial: Partial<Video> = {}): Video {
  seq += 1;
  return {
    id: `00000000-0000-4000-8000-${String(seq).padStart(12, '0')}`,
    title: `Match ${seq}`,
    status: 'done',
    source_type: 'youtube',
    source_url: null,
    size_bytes: 1_200_000_000,
    duration_ms: 5_652_000,
    fps: 25,
    width: 1280,
    height: 720,
    thumbnail_url: null,
    error: null,
    rights_confirmed_at: '2026-10-01T09:00:00Z',
    created_at: new Date(Date.UTC(2026, 9, 1, 9, 0, seq)).toISOString(),
    latest_job: null,
    ...partial,
  };
}

export function mockJob(video: Pick<Video, 'id'>, partial: Partial<MockJob> = {}): MockJob {
  return {
    id: `00000000-0000-4000-9000-${video.id.slice(-12)}`,
    video_id: video.id,
    status: 'inferring',
    progress: 0,
    stage: 'infer',
    segments: null,
    fps: null,
    eta_s: null,
    error: null,
    coverage: null,
    image_tag: 'mock',
    config: {},
    created_at: '2026-10-05T09:00:00Z',
    started_at: '2026-10-05T09:00:05Z',
    finished_at: null,
    ...partial,
  };
}

/** `VideoOut.latest_job` of a mock job (what GET /videos returns for it). */
export function mockLatestJob(job: MockJob): LatestJob {
  const live = toLatestJob(job);
  return {
    id: live.id,
    status: live.status,
    progress: live.progress,
    stage: live.stage,
    error: live.error,
    coverage: live.coverage,
    pitch_calibration: job.pitch_calibration ?? null,
  };
}

const RUNNING_JOB: readonly Job['status'][] = [
  'queued',
  'ingesting',
  'calibrating',
  'inferring',
  'postprocessing',
];

/** The event the server sends right after `subscribed` (current state of the job). */
export function jobSnapshotEvent(job: Job): WsJobEvent | null {
  const topic = `job:${job.id}`;
  if (job.status === 'done') return { topic, type: 'completed', data: { video_id: job.video_id } };
  if (job.status === 'cancelled') {
    return { topic, type: 'cancelled', data: { video_id: job.video_id } };
  }
  if (job.status === 'failed') {
    return {
      topic,
      type: 'failed',
      data: { code: job.error?.code ?? 'INTERNAL', retryable: job.error?.retryable ?? false },
    };
  }
  return {
    topic,
    type: 'progress',
    data: {
      stage: job.stage ?? job.status,
      pct: job.progress,
      fps: job.fps ?? null,
      eta_s: job.eta_s ?? null,
      segments: job.segments ?? null,
    },
  };
}

/** Push a job event to every socket subscribed to its topic. Returns the number of receivers. */
export function emitWs(db: MockVideoDb, event: WsJobEvent): number {
  let n = 0;
  for (const [client, topics] of db.sockets) {
    if (topics.has(event.topic)) {
      send(client, event);
      n += 1;
    }
  }
  return n;
}

/** Topics currently subscribed by any socket. */
export function wsTopics(db: MockVideoDb): string[] {
  return [...new Set([...db.sockets.values()].flatMap((t) => [...t]))];
}

function send(client: WsClient, frame: WsServerEvent): void {
  client.send(JSON.stringify(frame));
}

function problem(status: number, code: string, title: string) {
  return HttpResponse.json(
    { type: `https://docs.pitchvision.ai/errors/${code.toLowerCase()}`, title, status, code },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );
}

export interface VideoHandlerOptions {
  /** Dev server: advance running jobs every `tickMs` and stream progress over WS. */
  simulate?: { tickMs: number };
}

export function videoHandlers(db: MockVideoDb, opts: VideoHandlerOptions = {}) {
  const find = (id: string) => db.videos.find((v) => v.id === id && v.status !== 'deleted');
  const jobsOf = (videoId: string) =>
    db.jobs
      .filter((j) => j.video_id === videoId)
      .sort((a, b) => b.created_at.localeCompare(a.created_at));
  /** VideoOut as the API returns it: `latest_job` from the same "query". */
  const out = (v: Video): Video => {
    const latest = jobsOf(v.id)[0];
    return { ...v, latest_job: latest ? mockLatestJob(latest) : null };
  };
  /** Same as the API's cancel path: → cancelled, then the orchestrator publishes `cancelled`. */
  const cancel = (job: MockJob) => {
    job.status = 'cancelled';
    job.finished_at = new Date().toISOString();
    emitWs(db, { topic: `job:${job.id}`, type: 'cancelled', data: { video_id: job.video_id } });
  };
  const socket = ws.link('*/api/v1/ws');

  const wsHandler: WebSocketHandler = socket.addEventListener('connection', ({ client }) => {
    if (!client.url.searchParams.get('token')) {
      client.close(4401, 'unauthenticated');
      return;
    }
    const topics = new Set<string>();
    db.sockets.set(client, topics);
    db.log.push('WS connect');
    const control = (m: WsControlMessage) => send(client, m);
    client.addEventListener('message', (e) => {
      if (typeof e.data !== 'string') return;
      let op: { op?: string; topic?: string };
      try {
        op = JSON.parse(e.data) as typeof op;
      } catch {
        control({ type: 'error', topic: null, data: { code: 'VALIDATION_FAILED' } });
        return;
      }
      if (op.op === 'subscribe' && op.topic) {
        const job = db.jobs.find((j) => `job:${j.id}` === op.topic);
        if (!job) {
          control({
            type: 'error',
            topic: op.topic,
            data: { code: 'NOT_FOUND', detail: 'job not found' },
          });
          return;
        }
        topics.add(op.topic);
        db.log.push(`WS subscribe ${op.topic}`);
        control({ type: 'subscribed', topic: op.topic, data: { status: job.status } });
        const snapshot = jobSnapshotEvent(job);
        if (snapshot) send(client, snapshot);
      } else if (op.op === 'unsubscribe' && op.topic) {
        topics.delete(op.topic);
        db.log.push(`WS unsubscribe ${op.topic}`);
        control({ type: 'unsubscribed', topic: op.topic });
      } else if (op.op === 'ping') {
        control({ type: 'pong' });
      }
    });
    client.addEventListener('close', () => db.sockets.delete(client));
  });

  if (opts.simulate) startSimulation(db, opts.simulate.tickMs);

  return [
    http.get('*/api/v1/videos', ({ request }) => {
      const url = new URL(request.url);
      const limit = Math.min(100, Math.max(1, Number(url.searchParams.get('limit') ?? 20)));
      const offset = Number(url.searchParams.get('cursor') ?? 0) || 0;
      const all = db.videos
        .filter((v) => v.status !== 'deleted')
        .sort((a, b) => b.created_at.localeCompare(a.created_at));
      const items = all.slice(offset, offset + limit).map(out);
      const next = offset + limit < all.length ? String(offset + limit) : null;
      db.log.push(`GET /api/v1/videos cursor=${url.searchParams.get('cursor') ?? ''}`);
      return HttpResponse.json({ items, next_cursor: next });
    }),
    http.get('*/api/v1/videos/:id/jobs', ({ params }) => {
      db.log.push(`GET /api/v1/videos/${String(params.id)}/jobs`);
      if (!find(String(params.id))) return problem(404, 'NOT_FOUND', 'Video not found');
      return HttpResponse.json({ items: jobsOf(String(params.id)) });
    }),
    http.get('*/api/v1/videos/:id', ({ params }) => {
      const v = find(String(params.id));
      db.log.push(`GET /api/v1/videos/${String(params.id)}`);
      return v ? HttpResponse.json(out(v)) : problem(404, 'NOT_FOUND', 'Video not found');
    }),
    http.patch('*/api/v1/videos/:id', async ({ params, request }) => {
      const v = find(String(params.id));
      if (!v) return problem(404, 'NOT_FOUND', 'Video not found');
      const body = (await request.json()) as { title?: string };
      db.log.push(`PATCH /api/v1/videos/${v.id} ${JSON.stringify(body)}`);
      const title = body.title?.trim() ?? '';
      if (title.length < 1 || title.length > 200) {
        return problem(422, 'VALIDATION_FAILED', 'Title must be 1–200 characters');
      }
      v.title = title;
      return HttpResponse.json(out(v));
    }),
    http.delete('*/api/v1/videos/:id', ({ params }) => {
      const v = find(String(params.id));
      if (!v) return problem(404, 'NOT_FOUND', 'Video not found');
      db.log.push(`DELETE /api/v1/videos/${v.id}`);
      const active = jobsOf(v.id).find((j) => RUNNING_JOB.includes(j.status));
      if (active) cancel(active);
      v.status = 'deleted';
      return new HttpResponse(null, { status: 204 });
    }),
    http.get('*/api/v1/jobs/:id', ({ params }) => {
      const job = db.jobs.find((j) => j.id === params.id);
      return job ? HttpResponse.json(job) : problem(404, 'NOT_FOUND', 'Job not found');
    }),
    http.post('*/api/v1/jobs/:id/retry', ({ params }) => {
      const job = db.jobs.find((j) => j.id === params.id);
      if (!job) return problem(404, 'NOT_FOUND', 'Job not found');
      if (job.status !== 'failed' || job.error?.retryable === false) {
        return problem(409, 'CONFLICT', 'Job is not retryable');
      }
      db.log.push(`POST /api/v1/jobs/${job.id}/retry`);
      Object.assign(job, {
        status: 'queued',
        stage: null,
        error: null,
        eta_s: null,
        finished_at: null,
      } satisfies Partial<Job>);
      return HttpResponse.json(job, { status: 202 });
    }),
    http.post('*/api/v1/jobs/:id/cancel', ({ params }) => {
      const job = db.jobs.find((j) => j.id === params.id);
      if (!job) return problem(404, 'NOT_FOUND', 'Job not found');
      if (!RUNNING_JOB.includes(job.status)) return problem(409, 'CONFLICT', 'Job not running');
      db.log.push(`POST /api/v1/jobs/${job.id}/cancel`);
      cancel(job);
      return HttpResponse.json(job);
    }),
    wsHandler,
  ];
}

let simulation: ReturnType<typeof setInterval> | null = null;

/** Dev only: running jobs advance a few % per tick through the pipeline stages, then complete. */
function startSimulation(db: MockVideoDb, tickMs: number): void {
  if (simulation) clearInterval(simulation);
  simulation = setInterval(() => {
    for (const job of db.jobs) {
      if (!RUNNING_JOB.includes(job.status)) continue;
      const topic = `job:${job.id}`;
      job.progress = Math.min(100, job.progress + 1 + Math.random() * 2.5);
      const p = job.progress;
      job.stage = p < 4 ? 'normalize' : p < 8 ? 'calibrate' : p < 92 ? 'infer' : 'tiles';
      job.status = p < 8 ? 'calibrating' : p < 92 ? 'inferring' : 'postprocessing';
      job.eta_s = Math.round(((100 - p) / 2) * (tickMs / 1000) * 10);
      job.fps = 90 + Math.round(Math.random() * 12);
      job.segments = { done: Math.floor(p / 2.3), running: 4, total: 45 };
      if (p >= 100) {
        job.status = 'done';
        job.coverage = { main_wide_pct: 74 };
        job.pitch_calibration = 'ok';
        job.finished_at = new Date().toISOString();
        emitWs(db, { topic, type: 'completed', data: { video_id: job.video_id } });
        continue;
      }
      emitWs(db, {
        topic,
        type: 'progress',
        data: {
          stage: job.stage,
          pct: Math.round(p * 10) / 10,
          fps: job.fps,
          eta_s: job.eta_s,
          segments: job.segments,
        },
      });
    }
  }, tickMs);
}
