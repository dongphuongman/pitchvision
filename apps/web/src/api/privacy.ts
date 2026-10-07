import { queryOptions } from '@tanstack/react-query';
import { api } from './client';
import type { components } from './generated/openapi';

/**
 * SCR-40 Data & privacy, regional half (apps/api/openapi.json):
 *   GET  /privacy                                  → {videos, exports, data_export} (DLG-08 counts, D02 state)
 *   POST /privacy/data-exports                     → 202 DataExportOut (409 in_progress)
 *   GET  /privacy/data-exports/{id}/download       → {url, filename, expires_in} (409 not_ready, 410 expired)
 * Deleting the account / org is identity's (identityApi.deleteAccount / deleteOrg).
 */
export type PrivacySummary = components['schemas']['PrivacySummary'];
export type DataExportOut = components['schemas']['DataExportOut'];

function required<T>(data: T | undefined, what: string): T {
  if (data === undefined) throw new Error(`Empty ${what} response`);
  return data;
}

export const privacyKey = ['privacy'] as const;

export const privacyQuery = queryOptions({
  queryKey: privacyKey,
  queryFn: async ({ signal }) => {
    const { data } = await api.GET('/api/v1/privacy', { signal });
    return required(data, 'privacy');
  },
  // poll while an export is being built
  refetchInterval: (q) => {
    const s = q.state.data?.data_export?.status;
    return s === 'queued' || s === 'running' ? 5_000 : false;
  },
});

export async function requestDataExport(): Promise<DataExportOut> {
  const { data } = await api.POST('/api/v1/privacy/data-exports');
  return required(data, 'data export');
}

/** Presigned attachment URL (15 min) — fetched on click, never cached. */
export async function dataExportDownloadUrl(id: string): Promise<string> {
  const { data } = await api.GET('/api/v1/privacy/data-exports/{export_id}/download', {
    params: { path: { export_id: id } },
  });
  return required(data, 'download').url;
}
