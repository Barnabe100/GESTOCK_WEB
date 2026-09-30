import { lazy } from 'react';

import type { FrontendModule } from '@/core/modules/types';

export const salesModule: FrontendModule = {
  code: 'sales',
  navigation: [
    {
      key: 'sales',
      labelKey: 'nav.sales',
      group: 'sales',
      icon: 'pi pi-shopping-cart',
      path: '/sales',
      permission: 'sales.sale.view',
    },
    {
      key: 'payment-methods',
      labelKey: 'nav.paymentMethods',
      group: 'sales',
      icon: 'pi pi-wallet',
      path: '/sales/payment-methods',
      permission: 'sales.payment_method.manage',
    },
  ],
  routes: [
    { path: 'sales', component: lazy(() => import('./SalesPage')), permission: 'sales.sale.view' },
    {
      path: 'sales/payment-methods',
      component: lazy(() => import('./PaymentMethodsPage')),
      permission: 'sales.payment_method.manage',
    },
    {
      path: 'sales/new',
      component: lazy(() => import('./SalePage')),
      permission: 'sales.sale.create',
    },
    {
      path: 'sales/:id',
      component: lazy(() => import('./SalePage')),
      permission: 'sales.sale.view',
    },
  ],
};
