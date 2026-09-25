import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { PrimeReactProvider } from 'primereact/api';
import { Toast } from 'primereact/toast';
import { useRef, useState } from 'react';
import { RouterProvider } from 'react-router';

import { ToastContext } from '@/shared/ui/toast';

import { ConsoleAuthProvider } from './ConsoleAuth';
import { createConsoleRouter } from './router';

/**
 * Application de la console TechNova (ADR-0031) : séparée de l'application des entreprises
 * (aucun fournisseur d'authentification, de capacités ni de module des tenants), chargée à la
 * demande sous `/tech-admin`. Elle ne parle qu'à l'API de la console (`/platform-api`).
 */
export function ConsoleApp() {
  const toast = useRef<Toast>(null);
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
      }),
  );
  const [router] = useState(createConsoleRouter);
  return (
    <PrimeReactProvider>
      <QueryClientProvider client={queryClient}>
        <ToastContext.Provider value={toast}>
          <Toast ref={toast} position="top-right" />
          <ConsoleAuthProvider>
            <RouterProvider router={router} />
          </ConsoleAuthProvider>
        </ToastContext.Provider>
      </QueryClientProvider>
    </PrimeReactProvider>
  );
}
