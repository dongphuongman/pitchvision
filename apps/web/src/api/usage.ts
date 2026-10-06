import { queryOptions } from '@tanstack/react-query';
import { api } from './client';
import type { components } from './generated/openapi';

/**
 * Plan + monthly minutes (GET /usage, G.E06) and SCR-51 notifications
 * (GET /notifications, POST /notifications/read). The plan comes from the access token, so it
 * follows an org switch / token refresh.
 */
export type Usage = components['schemas']['Usage'];
export type NotificationList = components['schemas']['NotificationList'];
export type AppNotification = components['schemas']['NotificationOut'];

function required<T>(data: T | undefined, what: string): T {
  if (data === undefined) throw new Error(`Empty ${what} response`);
  return data;
}

export const usageQuery = queryOptions({
  queryKey: ['usage'] as const,
  queryFn: async ({ signal }) => {
    const { data } = await api.GET('/api/v1/usage', { signal });
    return required(data, 'usage');
  },
  staleTime: 60_000,
});

export const notificationsKey = ['notifications'] as const;

export const notificationsQuery = queryOptions({
  queryKey: notificationsKey,
  queryFn: async ({ signal }) => {
    const { data } = await api.GET('/api/v1/notifications', {
      params: { query: { limit: 20 } },
      signal,
    });
    return required(data, 'notifications');
  },
  refetchInterval: 60_000,
});

export async function markNotificationsRead(body: {
  ids?: string[];
  all?: boolean;
}): Promise<void> {
  await api.POST('/api/v1/notifications/read', {
    body: { ids: body.ids ?? [], all: body.all ?? false },
  });
}

export type UsageLevel = 'ok' | 'warn' | 'full';

/** G.E06: yellow from 80 %, red at 100 %. */
export function usageLevel(u: Pick<Usage, 'minutes_used' | 'minutes_limit'>): UsageLevel {
  if (u.minutes_limit <= 0 || u.minutes_used >= u.minutes_limit) return 'full';
  return u.minutes_used / u.minutes_limit >= 0.8 ? 'warn' : 'ok';
}
