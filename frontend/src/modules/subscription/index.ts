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
  // Rappels d'échéance (3.3-B4) : une source du centre de notifications unique (indicateur et
  // page `/notifications` du module Organisation), visible avec le droit de consulter
  // l'abonnement.
  notifications: [
    {
      key: 'subscription',
      labelKey: 'notifications.sources.subscription',
      permission: 'subscription.subscription.view',
      unreadPath: '/notifications/unread-count',
      queryKey: ['notifications'],
      refetchInterval: 5 * 60_000,
      panel: lazy(() => import('./ExpiryNotificationsPanel')),
    },
  ],
};
