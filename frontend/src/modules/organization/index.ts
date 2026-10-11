import { lazy } from 'react';

import type { FrontendModule } from '@/core/modules/types';

import { MEMBER_NOTIFICATIONS_KEY, MEMBER_NOTIFICATIONS_POLL_MS } from './notifications/api';
import { NotificationsBell } from './notifications/NotificationsBell';

export const organizationModule: FrontendModule = {
  code: 'organization',
  navigation: [
    {
      key: 'onboarding',
      labelKey: 'nav.onboarding',
      group: 'admin',
      icon: 'pi pi-flag',
      path: '/onboarding',
      permission: 'organization.onboarding.view',
    },
    {
      key: 'company',
      labelKey: 'nav.company',
      group: 'admin',
      icon: 'pi pi-building',
      path: '/organization/company',
      permission: 'organization.tenant.view',
    },
    {
      key: 'sites',
      labelKey: 'nav.sites',
      group: 'admin',
      icon: 'pi pi-map-marker',
      path: '/organization/sites',
      permission: 'organization.site.view',
    },
    {
      key: 'modules',
      labelKey: 'nav.modules',
      group: 'admin',
      icon: 'pi pi-th-large',
      path: '/organization/modules',
      permission: 'organization.module.view',
    },
  ],
  routes: [
    {
      path: 'onboarding',
      component: lazy(() => import('./OnboardingPage')),
      permission: 'organization.onboarding.view',
    },
    {
      path: 'organization/company',
      component: lazy(() => import('./CompanyPage')),
      permission: 'organization.tenant.view',
    },
    {
      path: 'organization/sites',
      component: lazy(() => import('./SitesPage')),
      permission: 'organization.site.view',
    },
    {
      path: 'organization/modules',
      component: lazy(() => import('./ModulesPage')),
      permission: 'organization.module.view',
    },
    // Centre de notifications UNIQUE : une section par source autorisée (voir `notifications`
    // des modules) ; sans source, la page l'indique.
    {
      path: 'notifications',
      component: lazy(() => import('./notifications/NotificationsCenterPage')),
    },
  ],
  // Indicateur unique (somme des sources autorisées) ; rien sans source.
  topbar: [{ key: 'notifications', component: NotificationsBell }],
  // Notifications des membres (ADR-0049 D8, palier R4) : lisibles par leur destinataire.
  notifications: [
    {
      key: 'activity',
      labelKey: 'memberNotifications.title',
      permission: 'organization.notification.view',
      unreadPath: '/member-notifications/unread-count',
      queryKey: MEMBER_NOTIFICATIONS_KEY,
      refetchInterval: MEMBER_NOTIFICATIONS_POLL_MS,
      panel: lazy(() => import('./notifications/MemberNotificationsPanel')),
    },
  ],
};
