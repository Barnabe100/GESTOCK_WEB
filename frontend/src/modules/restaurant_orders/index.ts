import { lazy } from 'react';

import type { FrontendModule } from '@/core/modules/types';

import { ORDER_CREATE, ORDER_VIEW, SETTINGS_MANAGE } from './api';

/**
 * Commandes de restauration (palier R2, ADR-0049) : affichées seulement si le backend déclare
 * le module effectif sur le site (il reste « à venir » tant que R2-E n'est pas validé).
 */
export const restaurantOrdersModule: FrontendModule = {
  code: 'restaurant.orders',
  navigation: [
    {
      key: 'restaurantOrders',
      labelKey: 'nav.restaurantOrders',
      group: 'restaurant',
      icon: 'pi pi-list',
      path: '/restaurant/orders',
      permission: ORDER_VIEW,
    },
  ],
  routes: [
    {
      path: 'restaurant/orders',
      component: lazy(() => import('./OrdersPage')),
      permission: ORDER_VIEW,
    },
    {
      path: 'restaurant/orders/new',
      component: lazy(() => import('./OrderCreatePage')),
      permission: ORDER_CREATE,
    },
    {
      path: 'restaurant/orders/settings',
      component: lazy(() => import('./OrderSettingsPage')),
      permission: SETTINGS_MANAGE,
    },
    {
      path: 'restaurant/orders/:id',
      component: lazy(() => import('./OrderPage')),
      permission: ORDER_VIEW,
    },
  ],
};
