import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/core/api/client';
import type { Page } from '@/shared/lib/serverTable';

/**
 * Recettes (palier R8, ADR-0053) : fiches techniques des plats. Quantités et montants en chaînes
 * décimales ; le serveur fait foi pour tout (règles, coûts, disponibilité, déduction à la vente).
 * Champs de coût ABSENTS des réponses sans `catalog.article.cost_view`.
 */

export const RECIPE_VIEW = 'restaurant.recipes.recipe.view';
export const RECIPE_MANAGE = 'restaurant.recipes.recipe.manage';
export const RECIPES_MODULE = 'restaurant.recipes';

export interface RecipeLine {
  article_id: string;
  reference: string;
  designation: string;
  unit: string;
  quantity: string;
  note: string | null;
  is_active: boolean;
  stock_managed: boolean;
}

export interface RecipeVersion {
  id: string;
  number: number;
  yield_quantity: string;
  instructions: string | null;
  created_at: string;
  created_by_name: string | null;
  lines: RecipeLine[];
}

export interface Recipe {
  id: string;
  article_id: string;
  reference: string;
  designation: string;
  unit: string;
  article_active: boolean;
  packaging_id: string | null;
  packaging_name: string | null;
  packaging_conversion: string | null;
  sale_price: string | null;
  is_active: boolean;
  notes: string | null;
  version_count: number;
  current_version: RecipeVersion | null;
  created_at: string;
  updated_at: string;
}

export interface RecipeSummary {
  id: string;
  article_id: string;
  reference: string;
  designation: string;
  unit: string;
  packaging_id: string | null;
  packaging_name: string | null;
  is_active: boolean;
  current_number: number | null;
  yield_quantity: string | null;
  ingredient_count: number;
  updated_at: string;
}

export interface CostLine {
  article_id: string;
  reference: string;
  designation: string;
  unit: string;
  quantity: string;
  unit_cost?: string;
  line_cost?: string;
  has_cost: boolean;
}

export interface SiteCost {
  site_id: string;
  site_name: string;
  yield_quantity: string;
  /** Absents sans `catalog.article.cost_view`. */
  batch_cost?: string;
  portion_cost?: string;
  margin?: string | null;
  margin_rate?: string | null;
  sale_price: string | null;
  complete: boolean;
  lines: CostLine[];
}

export interface SiteAvailability {
  site_id: string;
  site_name: string;
  recipes_enabled: boolean;
  units: number;
  limiting: string[];
  lines: {
    article_id: string;
    reference: string;
    designation: string;
    unit: string;
    required: string;
    available: string;
    in_assortment: boolean;
  }[];
}

export interface RecipeSheet {
  recipe_id: string;
  version_number: number;
  designation: string;
  packaging_name: string | null;
  quantity: string;
  portions: string;
  instructions: string | null;
  lines: {
    reference: string;
    designation: string;
    unit: string;
    quantity: string;
    note: string | null;
  }[];
}

export interface RecipeLineInput {
  article_id: string;
  quantity: string;
  note: string | null;
}

export interface RecipeVersionInput {
  yield_quantity: string;
  instructions: string | null;
  lines: RecipeLineInput[];
}

export interface RecipeCreateInput extends RecipeVersionInput {
  article_id: string;
  packaging_id: string | null;
  notes: string | null;
}

export const recipeKeys = {
  all: ['restaurant.recipes'] as const,
  list: ['restaurant.recipes', 'list'] as const,
  detail: (id: string) => ['restaurant.recipes', 'detail', id] as const,
};

export function useRecipes(query: string) {
  return useQuery({
    queryKey: [...recipeKeys.list, query],
    queryFn: ({ signal }) => api.get<Page<RecipeSummary>>(`/restaurant/recipes?${query}`, signal),
    placeholderData: keepPreviousData,
  });
}

export function useRecipe(id: string | undefined) {
  return useQuery({
    queryKey: recipeKeys.detail(id ?? ''),
    queryFn: ({ signal }) => api.get<Recipe>(`/restaurant/recipes/${id}`, signal),
    enabled: id !== undefined,
  });
}

export function useRecipeVersions(id: string, enabled: boolean) {
  return useQuery({
    queryKey: [...recipeKeys.detail(id), 'versions'],
    queryFn: ({ signal }) => api.get<RecipeVersion[]>(`/restaurant/recipes/${id}/versions`, signal),
    enabled,
  });
}

export function useRecipeCosts(id: string) {
  return useQuery({
    queryKey: [...recipeKeys.detail(id), 'costs'],
    queryFn: ({ signal }) => api.get<SiteCost[]>(`/restaurant/recipes/${id}/costs`, signal),
  });
}

export function useRecipeAvailability(id: string) {
  return useQuery({
    queryKey: [...recipeKeys.detail(id), 'availability'],
    queryFn: ({ signal }) =>
      api.get<SiteAvailability[]>(`/restaurant/recipes/${id}/availability`, signal),
  });
}

/** Fiche technique d'une ligne de commande (écran cuisine, D13). */
export function useRecipeSheet(
  target: { articleId: string; packagingId: string | null; quantity: string } | null,
) {
  const params = target
    ? new URLSearchParams({
        article_id: target.articleId,
        quantity: target.quantity,
        ...(target.packagingId ? { packaging_id: target.packagingId } : {}),
      }).toString()
    : '';
  return useQuery({
    queryKey: [...recipeKeys.all, 'sheet', params],
    queryFn: ({ signal }) => api.get<RecipeSheet>(`/restaurant/recipes/sheet?${params}`, signal),
    enabled: target !== null,
    retry: false,
  });
}

export function useRecipeMutations() {
  const qc = useQueryClient();
  const onSuccess = () => void qc.invalidateQueries({ queryKey: recipeKeys.all });
  return {
    create: useMutation({
      mutationFn: (input: RecipeCreateInput) => api.post<Recipe>('/restaurant/recipes', input),
      onSuccess,
    }),
    newVersion: useMutation({
      mutationFn: ({ id, input }: { id: string; input: RecipeVersionInput }) =>
        api.post<Recipe>(`/restaurant/recipes/${id}/versions`, input),
      onSuccess,
    }),
    updateNotes: useMutation({
      mutationFn: ({ id, notes }: { id: string; notes: string | null }) =>
        api.put<Recipe>(`/restaurant/recipes/${id}`, { notes }),
      onSuccess,
    }),
    setActive: useMutation({
      mutationFn: ({ id, active }: { id: string; active: boolean }) =>
        api.post<Recipe>(`/restaurant/recipes/${id}/${active ? 'activate' : 'deactivate'}`),
      onSuccess,
    }),
  };
}

/** Libellé d'une recette : plat et, pour une variante, son conditionnement. */
export function recipeLabel(r: { designation: string; packaging_name: string | null }): string {
  return r.packaging_name ? `${r.designation} — ${r.packaging_name}` : r.designation;
}
