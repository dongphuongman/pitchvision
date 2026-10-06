import { queryOptions } from '@tanstack/react-query';
import { api } from './client';
import type { components } from './generated/openapi';

/**
 * Exports (SCR-25 / SCR-26, apps/api/openapi.json):
 *
 *   POST   /exports                 → 202 ExportOut (Idempotency-Key; 402 PLAN_REQUIRED, 409 no_analysis,
 *                                     422 reason range | no_pitch_map)
 *   GET    /exports?status=         → ExportList (newest first; `expired` once past expires_at)
 *   GET    /exports/{id}/download   → {url, filename, expires_in} (409 not_ready, 410 expired)
 *   POST   /exports/{id}/cancel · /retry · DELETE /exports/{id}
 */
export type ExportOut = components['schemas']['ExportOut'];
export type ExportCreate = components['schemas']['ExportCreate'];
export type ExportLayers = components['schemas']['ExportLayers'];
export type ExportStatusFilter = 'all' | 'active' | 'done';

export const ACTIVE_EXPORT: ReadonlySet<ExportOut['status']> = new Set(['queued', 'running']);

function required<T>(data: T | undefined, what: string): T {
  if (data === undefined) throw new Error(`Empty ${what} response`);
  return data;
}

export const exportKeys = {
  all: ['exports'] as const,
  list: (status: ExportStatusFilter) => ['exports', 'list', status] as const,
};

export function exportsQuery(status: ExportStatusFilter) {
  return queryOptions({
    queryKey: exportKeys.list(status),
    queryFn: async ({ signal }) => {
      const { data } = await api.GET('/api/v1/exports', {
        params: { query: { status, limit: 100 } },
        signal,
      });
      return required(data, 'exports');
    },
    // SCR-26 E05: progress of running exports (no WS topic for exports).
    refetchInterval: (q) =>
      q.state.data?.items.some((e) => ACTIVE_EXPORT.has(e.status)) ? 3000 : false,
  });
}

export async function createExport(body: ExportCreate, idempotencyKey: string): Promise<ExportOut> {
  const { data } = await api.POST('/api/v1/exports', {
    body,
    headers: { 'Idempotency-Key': idempotencyKey },
  });
  return required(data, 'export');
}

export async function cancelExport(id: string): Promise<ExportOut> {
  const { data } = await api.POST('/api/v1/exports/{export_id}/cancel', {
    params: { path: { export_id: id } },
  });
  return required(data, 'export');
}

export async function retryExport(id: string): Promise<ExportOut> {
  const { data } = await api.POST('/api/v1/exports/{export_id}/retry', {
    params: { path: { export_id: id } },
  });
  return required(data, 'export');
}

export async function deleteExport(id: string): Promise<void> {
  await api.DELETE('/api/v1/exports/{export_id}', { params: { path: { export_id: id } } });
}

/** Presigned attachment URL (15 min) — fetched on click, never cached. */
export async function exportDownloadUrl(id: string): Promise<string> {
  const { data } = await api.GET('/api/v1/exports/{export_id}/download', {
    params: { path: { export_id: id } },
  });
  return required(data, 'download').url;
}
