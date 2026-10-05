import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import { RouterProvider } from '@tanstack/react-router';
import { useState } from 'react';
import { createQueryClient } from '@/api/queryClient';
import { createAppRouter } from '@/router';

export function App({ queryClient: injected }: { queryClient?: QueryClient } = {}) {
  const [queryClient] = useState(() => injected ?? createQueryClient());
  const [router] = useState(() => createAppRouter({ queryClient }));
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
}
