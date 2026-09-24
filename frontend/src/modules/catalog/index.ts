import { lazy } from 'react';

import type { FrontendModule } from '@/core/modules/types';

export const catalogModule: FrontendModule = {
  code: 'catalog',
  navigation: [
    {
      key: 'articles',
      labelKey: 'nav.articles',
      icon: 'pi pi-box',
      path: '/catalog/articles',
      permission: 'catalog.article.view',
    },
    {
      key: 'categories',
      labelKey: 'nav.categories',
      icon: 'pi pi-tags',
      path: '/catalog/categories',
      permission: 'catalog.category.view',
    },
  ],
  routes: [
    {
      path: 'catalog/articles',
      component: lazy(() => import('./ArticlesPage')),
      permission: 'catalog.article.view',
    },
    {
      path: 'catalog/categories',
      component: lazy(() => import('./CategoriesPage')),
      permission: 'catalog.category.view',
    },
  ],
};
