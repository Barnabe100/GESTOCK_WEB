import { lazy } from 'react';

import type { FrontendModule } from '@/core/modules/types';

/** Inventaires (module backend `inventory_count`) : rubrique Stock. */
export const inventoryCountModule: FrontendModule = {
  code: 'inventory_count',
  navigation: [
    {
      key: 'inventories',
      labelKey: 'nav.inventories',
      group: 'stock',
      icon: 'pi pi-clipboard',
      path: '/inventories',
      permission: 'inventory_count.inventory.view',
    },
  ],
  routes: [
    {
      path: 'inventories',
      component: lazy(() => import('./InventoriesPage')),
      permission: 'inventory_count.inventory.view',
    },
    {
      path: 'inventories/new',
      component: lazy(() => import('./InventoryCreatePage')),
      permission: 'inventory_count.inventory.create',
    },
    {
      path: 'inventories/:id',
      component: lazy(() => import('./InventoryPage')),
      permission: 'inventory_count.inventory.view',
    },
  ],
};
