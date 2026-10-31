import { lazy } from 'react';

import type { FrontendModule } from '@/core/modules/types';

import { TABLE_VIEW } from './api';

/**
 * Zones et tables des sites de restauration (palier R5, ADR-0049) : affichées seulement si le
 * backend déclare le module effectif (il reste « à venir » tant qu'il est planifié).
 */
export const restaurantTablesModule: FrontendModule = {
  code: 'restaurant.tables',
  navigation: [
    {
      key: 'restaurantTables',
      labelKey: 'nav.restaurantTables',
      group: 'restaurant',
      icon: 'pi pi-th-large',
      path: '/restaurant/tables',
      permission: TABLE_VIEW,
    },
  ],
  routes: [
    {
      path: 'restaurant/tables',
      component: lazy(() => import('./TablesPage')),
      permission: TABLE_VIEW,
    },
  ],
};
