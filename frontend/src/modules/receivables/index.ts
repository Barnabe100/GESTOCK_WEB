import { lazy } from 'react';

import type { FrontendModule } from '@/core/modules/types';

export const receivablesModule: FrontendModule = {
  code: 'receivables',
  navigation: [
    {
      key: 'receivables',
      labelKey: 'nav.receivables',
      group: 'sales',
      icon: 'pi pi-wallet',
      path: '/receivables',
      permission: 'receivables.receivable.view',
    },
  ],
  routes: [
    {
      path: 'receivables',
      component: lazy(() => import('./ReceivablesPage')),
      permission: 'receivables.receivable.view',
    },
  ],
};
