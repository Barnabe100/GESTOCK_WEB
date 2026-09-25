import { lazy } from 'react';

import type { FrontendModule } from '@/core/modules/types';

export const posModule: FrontendModule = {
  code: 'pos',
  navigation: [
    {
      key: 'pos',
      labelKey: 'nav.pos',
      group: 'sales',
      icon: 'pi pi-calculator',
      path: '/pos',
      permission: 'pos.terminal.use',
    },
  ],
  routes: [
    { path: 'pos', component: lazy(() => import('./PosPage')), permission: 'pos.terminal.use' },
  ],
};
