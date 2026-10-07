import { lazy } from 'react';

import type { FrontendModule } from '@/core/modules/types';

export const catalogModule: FrontendModule = {
  code: 'catalog',
  navigation: [
    {
      key: 'articles',
      labelKey: 'nav.articles',
      group: 'catalog',
      icon: 'pi pi-box',
      path: '/catalog/articles',
      permission: 'catalog.article.view',
    },
    {
      key: 'categories',
      labelKey: 'nav.categories',
      group: 'catalog',
      icon: 'pi pi-tags',
      path: '/catalog/categories',
      permission: 'catalog.category.view',
    },
    {
      // Recette, étape 1 (ADR-0046) : articles proposés par chaque site.
      key: 'assortment',
      labelKey: 'nav.assortment',
      group: 'catalog',
      icon: 'pi pi-th-large',
      path: '/catalog/assortment',
      permission: 'catalog.article.view',
    },
  ],
  routes: [
    {
      path: 'catalog/articles',
      component: lazy(() => import('./ArticlesPage')),
      permission: 'catalog.article.view',
    },
    {
      path: 'catalog/articles/:id',
      component: lazy(() => import('./ArticleDetailPage')),
      permission: 'catalog.article.view',
    },
    {
      path: 'catalog/assortment',
      component: lazy(() => import('./AssortmentPage')),
      permission: 'catalog.article.view',
    },
    {
      path: 'catalog/categories',
      component: lazy(() => import('./CategoriesPage')),
      permission: 'catalog.category.view',
    },
  ],
};
