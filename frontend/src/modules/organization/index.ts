import { lazy } from 'react';

import type { FrontendModule } from '@/core/modules/types';

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
  ],
};
