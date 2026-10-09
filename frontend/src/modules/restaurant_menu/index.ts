import { lazy } from 'react';

import type { FrontendModule } from '@/core/modules/types';

import { MENU_VIEW } from './api';

/** Menu des sites de restauration (palier R1, ADR-0049) : affiché si le module est effectif. */
export const restaurantMenuModule: FrontendModule = {
  code: 'restaurant.menu',
  navigation: [
    {
      key: 'restaurantMenu',
      labelKey: 'nav.restaurantMenu',
      group: 'restaurant',
      icon: 'pi pi-book',
      path: '/restaurant/menu',
      permission: MENU_VIEW,
    },
  ],
  routes: [
    {
      path: 'restaurant/menu',
      component: lazy(() => import('./MenuPage')),
      permission: MENU_VIEW,
    },
  ],
};
