import { Suspense, useMemo } from 'react';
import { Navigate, useRoutes } from 'react-router';

import { useAuth } from '@/core/auth/AuthContext';
import { CapabilitiesProvider } from '@/core/capabilities/CapabilitiesProvider';
import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { buildRoutes } from '@/core/modules/registry';
import { AppLayout } from '@/layouts/AppLayout';
import { NotFound } from '@/shared/ui/NotFound';

import { FRONTEND_MODULES } from './modules';
import { LoadingState } from '@/shared/ui/LoadingState';

function Spinner() {
  return <LoadingState />;
}

/** Routes générées depuis le registre des modules, filtrées par les capacités. */
function ModuleRoutes() {
  const { capabilities } = useCapabilities();
  const routes = useMemo(
    () => [
      ...buildRoutes(FRONTEND_MODULES, capabilities).map(({ path, component: Page }) => ({
        path,
        element: <Page />,
      })),
      { path: '*', element: <NotFound /> },
    ],
    [capabilities],
  );
  return <Suspense fallback={<Spinner />}>{useRoutes(routes)}</Suspense>;
}

export function ProtectedApp() {
  const auth = useAuth();
  if (auth.status === 'loading') return <Spinner />;
  if (auth.status === 'anonymous') return <Navigate to="/login" replace />;
  if (auth.user?.must_change_password) return <Navigate to="/change-password" replace />;
  if (!auth.tenantId) return <Navigate to="/select-tenant" replace />;

  return (
    <CapabilitiesProvider key={auth.tenantId} tenantId={auth.tenantId}>
      <AppLayout modules={FRONTEND_MODULES}>
        <ModuleRoutes />
      </AppLayout>
    </CapabilitiesProvider>
  );
}
