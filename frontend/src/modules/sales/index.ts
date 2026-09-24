import { lazy } from 'react';

import type { FrontendModule } from '@/core/modules/types';

export const salesModule: FrontendModule = {
  code: 'sales',
  navigation: [
    {
      key: 'sales',
      labelKey: 'nav.sales',
      group: 'sales',
      icon: 'pi pi-shopping-cart',
      path: '/sales',
      permission: 'sales.sale.view',
    },
  ],
  routes: [
    { path: 'sales', component: lazy(() => import('./SalesPage')), permission: 'sales.sale.view' },
    {
      path: 'sales/new',
      component: lazy(() => import('./SalePage')),
      permission: 'sales.sale.create',
    },
    {
      path: 'sales/:id',
      component: lazy(() => import('./SalePage')),
      permission: 'sales.sale.view',
    },
  ],
};
