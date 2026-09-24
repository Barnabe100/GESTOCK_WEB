import { lazy } from 'react';

import type { FrontendModule } from '@/core/modules/types';

export const stockModule: FrontendModule = {
  code: 'stock',
  navigation: [
    {
      key: 'stock-levels',
      labelKey: 'nav.stockLevels',
      icon: 'pi pi-warehouse',
      path: '/stock/levels',
      permission: 'stock.level.view',
    },
    {
      key: 'stock-entries',
      labelKey: 'nav.stockEntries',
      icon: 'pi pi-sign-in',
      path: '/stock/entries',
      permission: 'stock.entry.view',
    },
    {
      key: 'stock-exits',
      labelKey: 'nav.stockExits',
      icon: 'pi pi-sign-out',
      path: '/stock/exits',
      permission: 'stock.exit.view',
    },
    {
      key: 'stock-movements',
      labelKey: 'nav.stockMovements',
      icon: 'pi pi-history',
      path: '/stock/movements',
      permission: 'stock.movement.view',
    },
    {
      key: 'stock-exit-reasons',
      labelKey: 'nav.exitReasons',
      icon: 'pi pi-list',
      path: '/stock/exit-reasons',
      permission: 'stock.reason.manage',
    },
  ],
  routes: [
    {
      path: 'stock/levels',
      component: lazy(() => import('./StockLevelsPage')),
      permission: 'stock.level.view',
    },
    {
      path: 'stock/entries',
      component: lazy(() => import('./DocumentsPage').then((m) => ({ default: m.EntriesPage }))),
      permission: 'stock.entry.view',
    },
    {
      path: 'stock/entries/new',
      component: lazy(() => import('./DocumentPage').then((m) => ({ default: m.EntryPage }))),
      permission: 'stock.entry.create',
    },
    {
      path: 'stock/entries/:id',
      component: lazy(() => import('./DocumentPage').then((m) => ({ default: m.EntryPage }))),
      permission: 'stock.entry.view',
    },
    {
      path: 'stock/exits',
      component: lazy(() => import('./DocumentsPage').then((m) => ({ default: m.ExitsPage }))),
      permission: 'stock.exit.view',
    },
    {
      path: 'stock/exits/new',
      component: lazy(() => import('./DocumentPage').then((m) => ({ default: m.ExitPage }))),
      permission: 'stock.exit.create',
    },
    {
      path: 'stock/exits/:id',
      component: lazy(() => import('./DocumentPage').then((m) => ({ default: m.ExitPage }))),
      permission: 'stock.exit.view',
    },
    {
      path: 'stock/movements',
      component: lazy(() => import('./MovementsPage')),
      permission: 'stock.movement.view',
    },
    {
      path: 'stock/exit-reasons',
      component: lazy(() => import('./ExitReasonsPage')),
      permission: 'stock.reason.manage',
    },
  ],
};
