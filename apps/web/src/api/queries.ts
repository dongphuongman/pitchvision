import { queryOptions, useQuery } from '@tanstack/react-query';
import { api } from './client';

/** Central query-key factory; WS handlers use the same keys with queryClient.setQueryData. */
export const queryKeys = {
  meta: ['meta'] as const,
};

export const metaQuery = queryOptions({
  queryKey: queryKeys.meta,
  queryFn: async ({ signal }) => {
    const { data } = await api.GET('/api/v1/meta', { signal });
    if (!data) throw new Error('Empty /api/v1/meta response');
    return data;
  },
  staleTime: 10 * 60_000,
});

/** API build + contract schema version (shown on Settings → About). */
export function useMeta() {
  return useQuery(metaQuery);
}
