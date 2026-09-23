import { QueryClient } from '@tanstack/react-query';

import { ApiError } from '@/core/api/client';

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      // Pas de nouvelle tentative sur une erreur métier/autorisation (4xx).
      retry: (count, error) =>
        !(error instanceof ApiError && error.status >= 400 && error.status < 500) && count < 2,
    },
  },
});
