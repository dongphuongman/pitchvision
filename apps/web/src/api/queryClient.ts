import { QueryClient } from '@tanstack/react-query';
import { isApiError } from './errors';

const MAX_RETRIES = 2;

/** Retry transient failures only; 4xx business errors surface immediately. */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (failureCount >= MAX_RETRIES) return false;
  return isApiError(error) ? error.retryable : true;
}

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        gcTime: 5 * 60_000,
        retry: shouldRetry,
        retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 8000),
        // Live job progress arrives over WebSocket (AD §11), not focus refetch storms.
        refetchOnWindowFocus: false,
      },
      mutations: { retry: false },
    },
  });
}
