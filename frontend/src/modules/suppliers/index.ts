import { lazy } from 'react';

import type { FrontendModule } from '@/core/modules/types';

export const suppliersModule: FrontendModule = {
  code: 'suppliers',
  navigation: [
    {
      key: 'suppliers',
      labelKey: 'nav.suppliers',
      group: 'catalog',
      icon: 'pi pi-truck',
      path: '/suppliers',
      permission: 'suppliers.supplier.view',
    },
  ],
  routes: [
    {
      path: 'suppliers',
      component: lazy(() => import('./SuppliersPage')),
      permission: 'suppliers.supplier.view',
    },
  ],
};
