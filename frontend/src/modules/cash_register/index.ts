import { lazy } from 'react';

import type { FrontendModule } from '@/core/modules/types';

export const cashRegisterModule: FrontendModule = {
  code: 'cash_register',
  navigation: [
    {
      key: 'cash-registers',
      labelKey: 'nav.cashRegisters',
      group: 'cash',
      icon: 'pi pi-box',
      path: '/cash/registers',
      permission: 'cash_register.register.view',
    },
    {
      key: 'cash-sessions',
      labelKey: 'nav.cashSessions',
      group: 'cash',
      icon: 'pi pi-clock',
      path: '/cash/sessions',
      permission: 'cash_register.session.view',
    },
    {
      key: 'cash-journal',
      labelKey: 'nav.cashJournal',
      group: 'cash',
      icon: 'pi pi-list',
      path: '/cash/journal',
      permission: 'cash_register.session.view',
    },
  ],
  routes: [
    {
      path: 'cash/registers',
      component: lazy(() => import('./RegistersPage')),
      permission: 'cash_register.register.view',
    },
    {
      path: 'cash/sessions',
      component: lazy(() => import('./SessionsPage')),
      permission: 'cash_register.session.view',
    },
    {
      path: 'cash/sessions/:id',
      component: lazy(() => import('./SessionPage')),
      permission: 'cash_register.session.view',
    },
    {
      path: 'cash/journal',
      component: lazy(() => import('./JournalPage')),
      permission: 'cash_register.session.view',
    },
  ],
};
