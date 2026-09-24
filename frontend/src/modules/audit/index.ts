import { lazy } from 'react';

import type { FrontendModule } from '@/core/modules/types';

export const auditModule: FrontendModule = {
  code: 'audit',
  navigation: [
    {
      key: 'audit',
      labelKey: 'nav.audit',
      group: 'admin',
      icon: 'pi pi-history',
      path: '/audit',
      permission: 'audit.log.view',
    },
  ],
  routes: [
    {
      path: 'audit',
      component: lazy(() => import('./AuditLogPage')),
      permission: 'audit.log.view',
    },
  ],
};
