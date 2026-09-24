import { lazy } from 'react';

import type { FrontendModule } from '@/core/modules/types';

export const alertsModule: FrontendModule = {
  code: 'alerts',
  navigation: [
    {
      key: 'stock-alerts',
      labelKey: 'nav.stockAlerts',
      group: 'stock',
      icon: 'pi pi-bell',
      path: '/alerts/stock',
      permission: 'alerts.stock.view',
    },
  ],
  routes: [
    {
      path: 'alerts/stock',
      component: lazy(() => import('./StockAlertsPage')),
      permission: 'alerts.stock.view',
    },
  ],
};
