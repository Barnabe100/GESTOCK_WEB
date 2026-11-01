import { lazy } from 'react';

import type { FrontendModule } from '@/core/modules/types';

import { STATION_MANAGE, STATION_VIEW } from './api';

/**
 * Cuisine des sites de restauration (palier R6, ADR-0050) : affichée seulement si le backend
 * déclare le module effectif sur le site (« à venir » tant qu'il est planifié).
 */
export const restaurantKitchenModule: FrontendModule = {
  code: 'restaurant.kitchen',
  navigation: [
    {
      key: 'restaurantKitchen',
      labelKey: 'nav.restaurantKitchen',
      group: 'restaurant',
      icon: 'pi pi-stopwatch',
      path: '/restaurant/kitchen',
      permission: STATION_VIEW,
    },
    {
      key: 'restaurantStations',
      labelKey: 'nav.restaurantStations',
      group: 'restaurant',
      icon: 'pi pi-sitemap',
      path: '/restaurant/kitchen/stations',
      permission: STATION_MANAGE,
    },
  ],
  routes: [
    {
      path: 'restaurant/kitchen',
      component: lazy(() => import('./KitchenPage')),
      permission: STATION_VIEW,
    },
    {
      path: 'restaurant/kitchen/stations',
      component: lazy(() => import('./StationsPage')),
      permission: STATION_MANAGE,
    },
  ],
};
