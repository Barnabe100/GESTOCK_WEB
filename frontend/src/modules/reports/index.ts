import { lazy } from 'react';

import type { FrontendModule } from '@/core/modules/types';

import { REPORT_VIEW } from './api';

/** Palier R10 (ADR-0055) : rapports des ventes (consultation et exports). */
export const reportsModule: FrontendModule = {
  code: 'reports',
  navigation: [
    {
      key: 'reports',
      labelKey: 'nav.reports',
      group: 'reports',
      icon: 'pi pi-chart-bar',
      path: '/reports',
      permission: REPORT_VIEW,
    },
  ],
  routes: [
    {
      path: 'reports',
      component: lazy(() => import('./ReportsPage')),
      permission: REPORT_VIEW,
    },
  ],
};
