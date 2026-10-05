import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, RouterProvider } from '@tanstack/react-router';
import { render } from '@testing-library/react';
import { createAppRouter } from '@/router';

/** Renders the full app (router + shell) at `url` with in-memory history. */
export function renderApp(url: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  });
  const history = createMemoryHistory({ initialEntries: [url] });
  const router = createAppRouter({ queryClient, history });
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { ...utils, router, history, queryClient };
}
