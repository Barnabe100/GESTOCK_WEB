import { lazy } from 'react';

import type { FrontendModule } from '@/core/modules/types';

export const dashboardModule: FrontendModule = {
  code: 'dashboard',
  navigation: [{ key: 'dashboard', labelKey: 'nav.dashboard', icon: 'pi pi-home', path: '/' }],
  routes: [{ path: '', component: lazy(() => import('./DashboardPage')) }],
};
