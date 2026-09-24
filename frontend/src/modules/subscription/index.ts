import { lazy } from 'react';

import type { FrontendModule } from '@/core/modules/types';

export const subscriptionModule: FrontendModule = {
  code: 'subscription',
  navigation: [
    {
      key: 'subscription',
      labelKey: 'nav.subscription',
      group: 'admin',
      icon: 'pi pi-credit-card',
      path: '/subscription',
      permission: 'subscription.subscription.view',
    },
  ],
  routes: [
    {
      path: 'subscription',
      component: lazy(() => import('./SubscriptionPage')),
      permission: 'subscription.subscription.view',
    },
  ],
};
