import { lazy } from 'react';

import type { FrontendModule } from '@/core/modules/types';

export const usersModule: FrontendModule = {
  code: 'users',
  navigation: [
    {
      key: 'members',
      labelKey: 'nav.members',
      icon: 'pi pi-users',
      path: '/users/members',
      permission: 'users.member.view',
    },
    {
      key: 'roles',
      labelKey: 'nav.roles',
      icon: 'pi pi-shield',
      path: '/users/roles',
      permission: 'users.role.view',
    },
  ],
  routes: [
    {
      path: 'users/members',
      component: lazy(() => import('./MembersPage')),
      permission: 'users.member.view',
    },
    {
      path: 'users/roles',
      component: lazy(() => import('./RolesPage')),
      permission: 'users.role.view',
    },
  ],
};
