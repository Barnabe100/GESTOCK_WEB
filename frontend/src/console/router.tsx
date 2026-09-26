import { createBrowserRouter, Navigate } from 'react-router';

import { NotFound } from '@/shared/ui/NotFound';

import { CONSOLE_BASE, ConsoleLayout } from './ConsoleLayout';
import { CatalogPage } from './pages/CatalogPage';
import { ConsoleDashboardPage } from './pages/ConsoleDashboardPage';
import { ConsoleLoginPage } from './pages/ConsoleLoginPage';
import { PaymentDetailPage } from './pages/PaymentDetailPage';
import { PaymentsPage } from './pages/PaymentsPage';
import { PlanDetailPage } from './pages/PlanDetailPage';
import { PlansPage } from './pages/PlansPage';
import { PlatformAuditPage } from './pages/PlatformAuditPage';
import { TenantDetailPage } from './pages/TenantDetailPage';
import { TenantsPage } from './pages/TenantsPage';

/** Routes de la console TechNova (hors application des entreprises). */
export const consoleRoutes = [
  { path: `${CONSOLE_BASE}/login`, element: <ConsoleLoginPage /> },
  {
    path: CONSOLE_BASE,
    element: <ConsoleLayout />,
    children: [
      { index: true, element: <Navigate to="dashboard" replace /> },
      { path: 'dashboard', element: <ConsoleDashboardPage /> },
      { path: 'plans', element: <PlansPage /> },
      { path: 'plans/:code', element: <PlanDetailPage /> },
      { path: 'catalog', element: <CatalogPage /> },
      { path: 'tenants', element: <TenantsPage /> },
      { path: 'tenants/:id', element: <TenantDetailPage /> },
      { path: 'payments', element: <PaymentsPage /> },
      { path: 'payments/:id', element: <PaymentDetailPage /> },
      { path: 'audit', element: <PlatformAuditPage /> },
      { path: '*', element: <NotFound /> },
    ],
  },
  { path: '*', element: <Navigate to={`${CONSOLE_BASE}/dashboard`} replace /> },
];

export const createConsoleRouter = () => createBrowserRouter(consoleRoutes);
