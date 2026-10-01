import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/core/api/client';
import type { Page } from '@/shared/lib/serverTable';

export interface Category {
  id: string;
  name: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

/** Lot 3-A : permissions distinctes (le serveur contrôle chaque champ et chaque réponse). */
export const ARTICLE_UPDATE = 'catalog.article.update';
export const PRICE_UPDATE = 'catalog.article.price_update';
export const COST_VIEW = 'catalog.article.cost_view';

/** Montants et quantités : chaînes décimales (aucun calcul côté client). */
export interface Article {
  id: string;
  reference: string;
  designation: string;
  category_id: string;
  category_name: string;
  unit: string;
  main_supplier_id: string | null;
  main_supplier_name: string | null;
  /** Coût interne : ABSENT de la réponse sans `catalog.article.cost_view`. */
  purchase_price?: string;
  sale_price: string;
  min_stock: string;
  max_stock: string | null;
  description: string | null;
  barcode: string | null;
  is_active: boolean;
  /** `false` : article / service vendu sans stock (aucun mouvement, aucun contrôle). */
  stock_managed: boolean;
  /** Lot 3-B : `false` = quantités vendues entières seulement (contrôle serveur). */
  decimal_quantity_allowed: boolean;
  created_at: string;
  updated_at: string;
}

/** Champs envoyés : seulement ceux que l'utilisateur peut modifier (le serveur revérifie). */
export interface ArticleInput {
  reference?: string;
  designation?: string;
  category_id?: string;
  unit?: string;
  main_supplier_id?: string | null;
  purchase_price?: string;
  sale_price?: string;
  min_stock?: string;
  max_stock?: string | null;
  description?: string;
  barcode?: string;
  stock_managed?: boolean;
  decimal_quantity_allowed?: boolean;
}

/**
 * Conditionnement de vente (Lot 3-B) : quantité de base = quantité × `conversion`. Jamais
 * supprimé (désactivé) ; `in_use` : figure sur une vente, sa conversion est alors figée.
 */
export interface Packaging {
  id: string;
  article_id: string;
  name: string;
  conversion: string;
  /** `null` : prix NON CONFIGURÉ — conditionnement invendable (≠ prix configuré à 0). */
  sale_price: string | null;
  is_active: boolean;
  in_use: boolean;
  created_at: string;
  updated_at: string;
}

/** Champs envoyés : seulement ceux que l'utilisateur peut modifier (le serveur revérifie). */
export interface PackagingInput {
  name?: string;
  conversion?: string;
  sale_price?: string;
}

/** Changement de prix lu dans le journal d'audit (prix d'achat : avec `cost_view`). */
export interface PriceChange {
  id: string;
  occurred_at: string;
  user_name: string | null;
  sale_price_before: string | null;
  sale_price_after: string | null;
  purchase_price_before?: string | null;
  purchase_price_after?: string | null;
}

export const catalogKeys = {
  categories: ['catalog', 'categories'] as const,
  articles: ['catalog', 'articles'] as const,
  packagings: ['catalog', 'packagings'] as const,
};

export function useCategories(query: string, enabled = true) {
  return useQuery({
    queryKey: [...catalogKeys.categories, query],
    queryFn: ({ signal }) => api.get<Page<Category>>(`/catalog/categories?${query}`, signal),
    placeholderData: keepPreviousData,
    enabled,
  });
}

export function useSaveCategory() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, name }: { id?: string; name: string }) =>
      id
        ? api.patch<Category>(`/catalog/categories/${id}`, { name })
        : api.post<Category>('/catalog/categories', { name }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['catalog'] }),
  });
}

export function useSetCategoryActive() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, active }: { id: string; active: boolean }) =>
      api.post<Category>(`/catalog/categories/${id}/${active ? 'activate' : 'deactivate'}`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['catalog'] }),
  });
}

export function useArticles(query: string) {
  return useQuery({
    queryKey: [...catalogKeys.articles, query],
    queryFn: ({ signal }) => api.get<Page<Article>>(`/catalog/articles?${query}`, signal),
    placeholderData: keepPreviousData,
  });
}

export function useArticle(id: string | undefined) {
  return useQuery({
    queryKey: [...catalogKeys.articles, 'detail', id],
    queryFn: ({ signal }) => api.get<Article>(`/catalog/articles/${id}`, signal),
    enabled: id !== undefined,
  });
}

export function usePriceHistory(id: string, query: string, enabled: boolean) {
  return useQuery({
    queryKey: [...catalogKeys.articles, 'price-history', id, query],
    queryFn: ({ signal }) =>
      api.get<Page<PriceChange>>(`/catalog/articles/${id}/price-history?${query}`, signal),
    placeholderData: keepPreviousData,
    enabled,
  });
}

export function useSaveArticle() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id?: string; input: ArticleInput }) =>
      id
        ? api.patch<Article>(`/catalog/articles/${id}`, input)
        : api.post<Article>('/catalog/articles', input),
    onSuccess: () => void qc.invalidateQueries({ queryKey: catalogKeys.articles }),
  });
}

export function useSetArticleActive() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, active }: { id: string; active: boolean }) =>
      api.post<Article>(`/catalog/articles/${id}/${active ? 'activate' : 'deactivate'}`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: catalogKeys.articles }),
  });
}

export function usePackagings(articleId: string | undefined, query: string, enabled = true) {
  return useQuery({
    queryKey: [...catalogKeys.packagings, articleId, query],
    queryFn: ({ signal }) =>
      api.get<Page<Packaging>>(`/catalog/articles/${articleId}/packagings?${query}`, signal),
    enabled: enabled && articleId !== undefined,
  });
}

export function useSavePackaging(articleId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id?: string; input: PackagingInput }) =>
      id
        ? api.patch<Packaging>(`/catalog/packagings/${id}`, input)
        : api.post<Packaging>(`/catalog/articles/${articleId}/packagings`, input),
    onSuccess: () => void qc.invalidateQueries({ queryKey: catalogKeys.packagings }),
  });
}

export function useSetPackagingActive() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, active }: { id: string; active: boolean }) =>
      api.post<Packaging>(`/catalog/packagings/${id}/${active ? 'activate' : 'deactivate'}`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: catalogKeys.packagings }),
  });
}
