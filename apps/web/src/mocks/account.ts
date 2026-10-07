import { http, HttpResponse } from 'msw';
import type { ExportCreate, ExportOut } from '@/api/exports';
import type { DataExportOut } from '@/api/privacy';
import type { AppNotification, Usage } from '@/api/usage';
import type { MockVideoDb } from './videos';

/**
 * Exports (SCR-25 / SCR-26), usage (G.E06) and notifications (SCR-51) on top of the videos DB.
 * Exports finish on the next list read after `exportTicks` reads (simulated render).
 */
export interface MockAccount {
  usage: Usage;
  exports: ExportOut[];
  notifications: AppNotification[];
  /** list reads before a queued / running export is done (0 = stays running). */
  exportTicks: number;
  /** SCR-40 D02: the user's data exports (newest last); a queued one reads done on the next GET /privacy. */
  dataExports: DataExportOut[];
}

function monthBounds(now = new Date()): [string, string] {
  const start = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1));
  const end = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + 1, 1));
  return [start.toISOString(), end.toISOString()];
}

export function createAccount(partial: Partial<MockAccount> = {}): MockAccount {
  const [start, end] = monthBounds();
  return {
    usage: {
      plan: 'free',
      minutes_limit: 120,
      minutes_used: 62,
      period_start: start,
      resets_at: end,
      export_max_height: 720,
      export_watermark: true,
    },
    exports: [],
    notifications: [],
    exportTicks: 0,
    dataExports: [],
    ...partial,
  };
}

function problem(status: number, code: string, ext: Record<string, unknown> = {}) {
  return HttpResponse.json(
    {
      type: `https://docs.pitchvision.ai/errors/${code.toLowerCase()}`,
      title: code,
      status,
      code,
      ...ext,
    },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );
}

let seq = 0;
const ticks = new Map<string, number>();

export function accountHandlers(acct: MockAccount, videos: MockVideoDb) {
  const find = (id: unknown) => acct.exports.find((e) => e.id === id);
  return [
    http.get('*/api/v1/usage', () => HttpResponse.json(acct.usage)),
    http.get('*/api/v1/privacy', () => {
      const latest = acct.dataExports.at(-1) ?? null;
      if (latest?.status === 'queued') {
        const now = Date.now();
        Object.assign(latest, {
          status: 'done',
          size_bytes: 1_250_000,
          finished_at: new Date(now).toISOString(),
          expires_at: new Date(now + 7 * 86_400_000).toISOString(),
        });
      }
      return HttpResponse.json({
        videos: videos.videos.filter((v) => v.status !== 'deleted').length,
        exports: acct.exports.length,
        data_export: latest,
      });
    }),
    http.post('*/api/v1/privacy/data-exports', () => {
      const latest = acct.dataExports.at(-1);
      if (latest?.status === 'queued' || latest?.status === 'running') {
        return problem(409, 'CONFLICT', { reason: 'in_progress' });
      }
      seq += 1;
      const d: DataExportOut = {
        id: `dx-${String(seq)}`,
        scope: 'org',
        status: 'queued',
        size_bytes: null,
        error: null,
        created_at: new Date().toISOString(),
        finished_at: null,
        expires_at: null,
      };
      acct.dataExports.push(d);
      videos.log.push('POST /api/v1/privacy/data-exports');
      return HttpResponse.json(d, { status: 202 });
    }),
    http.get('*/api/v1/privacy/data-exports/:id/download', ({ params }) => {
      const d = acct.dataExports.find((x) => x.id === params.id);
      if (!d) return problem(404, 'NOT_FOUND');
      if (d.status !== 'done') return problem(409, 'CONFLICT', { reason: 'not_ready' });
      return HttpResponse.json({
        url: `https://storage.example/data/${d.id}.zip`,
        filename: 'pitchvision-data.zip',
        expires_in: 900,
      });
    }),
    http.get('*/api/v1/notifications', () =>
      HttpResponse.json({
        items: [...acct.notifications].sort((a, b) => b.created_at.localeCompare(a.created_at)),
        unread_count: acct.notifications.filter((n) => !n.read_at).length,
        next_cursor: null,
      }),
    ),
    http.post('*/api/v1/notifications/read', async ({ request }) => {
      const body = (await request.json()) as { ids?: string[]; all?: boolean };
      const now = new Date().toISOString();
      for (const n of acct.notifications) {
        if (!n.read_at && (body.all || body.ids?.includes(n.id))) n.read_at = now;
      }
      videos.log.push(
        `POST /api/v1/notifications/read ${body.all ? 'all' : (body.ids ?? []).join(',')}`,
      );
      return new HttpResponse(null, { status: 204 });
    }),
    http.post('*/api/v1/exports', async ({ request }) => {
      const body = (await request.json()) as ExportCreate;
      const video = videos.videos.find((v) => v.id === body.video_id);
      if (!video) return problem(404, 'NOT_FOUND');
      if (body.quality === 1080 && acct.usage.export_max_height < 1080) {
        return problem(402, 'PLAN_REQUIRED', { reason: 'export_quality', plan: acct.usage.plan });
      }
      seq += 1;
      const from = body.from_ms ?? 0;
      const to = body.to_ms ?? video.duration_ms ?? 0;
      const e: ExportOut = {
        id: `exp-${String(seq)}`,
        video_id: video.id,
        video_title: video.title ?? null,
        kind: 'video',
        status: 'queued',
        progress: 0,
        filename: `${(video.title ?? 'video').toLowerCase().replace(/[^a-z0-9]+/g, '_')}_full.mp4`,
        size_bytes: null,
        from_ms: from,
        to_ms: to,
        layout: body.layout,
        quality: body.quality,
        watermark: acct.usage.export_watermark,
        layers: { ...body.layers },
        error: null,
        created_by: null,
        created_at: new Date().toISOString(),
        finished_at: null,
        expires_at: null,
      };
      acct.exports.push(e);
      videos.log.push(`POST /api/v1/exports ${JSON.stringify(body)}`);
      return HttpResponse.json(e, { status: 202 });
    }),
    http.get('*/api/v1/exports', ({ request }) => {
      const status = new URL(request.url).searchParams.get('status') ?? 'all';
      for (const e of acct.exports) {
        if ((e.status === 'queued' || e.status === 'running') && acct.exportTicks > 0) {
          const n = (ticks.get(e.id) ?? 0) + 1;
          ticks.set(e.id, n);
          e.status = 'running';
          e.progress = Math.min(99, Math.round((100 * n) / acct.exportTicks));
          if (n >= acct.exportTicks) {
            const now = Date.now();
            Object.assign(e, {
              status: 'done',
              progress: 100,
              size_bytes: 48_000_000,
              finished_at: new Date(now).toISOString(),
              expires_at: new Date(now + 7 * 86_400_000).toISOString(),
            });
          }
        }
      }
      const items = acct.exports
        .filter((e) =>
          status === 'active'
            ? e.status === 'queued' || e.status === 'running'
            : status === 'done'
              ? e.status === 'done'
              : true,
        )
        .sort((a, b) => b.created_at.localeCompare(a.created_at));
      return HttpResponse.json({ items, next_cursor: null });
    }),
    http.get('*/api/v1/exports/:id/download', ({ params }) => {
      const e = find(params.id);
      if (!e) return problem(404, 'NOT_FOUND');
      if (e.status !== 'done') return problem(409, 'CONFLICT', { reason: 'not_ready' });
      return HttpResponse.json({
        url: `https://storage.test/${e.id}.mp4`,
        filename: e.filename,
        expires_in: 900,
      });
    }),
    http.post('*/api/v1/exports/:id/cancel', ({ params }) => {
      const e = find(params.id);
      if (!e) return problem(404, 'NOT_FOUND');
      e.status = 'cancelled';
      videos.log.push(`POST /api/v1/exports/${e.id}/cancel`);
      return HttpResponse.json(e);
    }),
    http.post('*/api/v1/exports/:id/retry', ({ params }) => {
      const e = find(params.id);
      if (!e) return problem(404, 'NOT_FOUND');
      Object.assign(e, { status: 'queued', progress: 0, error: null });
      videos.log.push(`POST /api/v1/exports/${e.id}/retry`);
      return HttpResponse.json(e, { status: 202 });
    }),
    http.delete('*/api/v1/exports/:id', ({ params }) => {
      acct.exports = acct.exports.filter((e) => e.id !== params.id);
      videos.log.push(`DELETE /api/v1/exports/${String(params.id)}`);
      return new HttpResponse(null, { status: 204 });
    }),
  ];
}
