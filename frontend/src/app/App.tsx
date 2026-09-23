import { QueryClientProvider } from '@tanstack/react-query';
import { PrimeReactProvider } from 'primereact/api';
import { RouterProvider } from 'react-router';

import { queryClient } from '@/app/queryClient';
import { router } from '@/app/router';

export function App() {
  return (
    <PrimeReactProvider>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </PrimeReactProvider>
  );
}
