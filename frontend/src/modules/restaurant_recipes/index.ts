import { lazy } from 'react';

import type { FrontendModule } from '@/core/modules/types';

import { RECIPE_MANAGE, RECIPE_VIEW } from './api';

/** Recettes des plats (palier R8, ADR-0053) : affiché si le module est effectif. */
export const restaurantRecipesModule: FrontendModule = {
  code: 'restaurant.recipes',
  navigation: [
    {
      key: 'restaurantRecipes',
      labelKey: 'nav.restaurantRecipes',
      group: 'restaurant',
      icon: 'pi pi-list-check',
      path: '/restaurant/recipes',
      permission: RECIPE_VIEW,
    },
  ],
  routes: [
    {
      path: 'restaurant/recipes',
      component: lazy(() => import('./RecipesPage')),
      permission: RECIPE_VIEW,
    },
    {
      path: 'restaurant/recipes/new',
      component: lazy(() => import('./NewRecipePage')),
      permission: RECIPE_MANAGE,
    },
    {
      path: 'restaurant/recipes/:id',
      component: lazy(() => import('./RecipePage')),
      permission: RECIPE_VIEW,
    },
  ],
};
