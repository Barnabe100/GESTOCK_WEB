import { QueryClientProvider } from '@tanstack/react-query';
import { PrimeReactProvider } from 'primereact/api';
import { Toast } from 'primereact/toast';
import { useRef } from 'react';
import { RouterProvider } from 'react-router';

import { queryClient } from '@/app/queryClient';
import { router } from '@/app/router';
import { AuthProvider } from '@/core/auth/AuthProvider';
import { ToastContext } from '@/shared/ui/toast';

export function App() {
  const toast = useRef<Toast>(null);
  return (
    <PrimeReactProvider>
      <QueryClientProvider client={queryClient}>
        <ToastContext.Provider value={toast}>
          <Toast ref={toast} position="top-right" />
          <AuthProvider>
            <RouterProvider router={router} />
          </AuthProvider>
        </ToastContext.Provider>
      </QueryClientProvider>
    </PrimeReactProvider>
  );
}
