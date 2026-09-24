import { lazy } from 'react';

import type { FrontendModule } from '@/core/modules/types';

export const customersModule: FrontendModule = {
  code: 'customers',
  navigation: [
    {
      key: 'customers',
      labelKey: 'nav.customers',
      icon: 'pi pi-id-card',
      path: '/customers',
      permission: 'customers.customer.view',
    },
  ],
  routes: [
    {
      path: 'customers',
      component: lazy(() => import('./CustomersPage')),
      permission: 'customers.customer.view',
    },
    {
      path: 'customers/:id',
      component: lazy(() => import('./CustomerDetailPage')),
      permission: 'customers.customer.view',
    },
  ],
};
