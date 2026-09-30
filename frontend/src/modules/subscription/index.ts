import { lazy } from 'react';

import type { FrontendModule } from '@/core/modules/types';

import { NotificationsBell } from './notificationDisplay';

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
    {
      path: 'notifications',
      component: lazy(() => import('./NotificationsPage')),
      permission: 'subscription.subscription.view',
    },
  ],
  // Rappels d'échéance (3.3-B4) : visibles avec le droit de consulter l'abonnement.
  topbar: [
    {
      key: 'notifications',
      component: NotificationsBell,
      permission: 'subscription.subscription.view',
    },
  ],
};
