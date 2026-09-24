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
  purchase_price: string;
  sale_price: string;
  min_stock: string;
  max_stock: string | null;
  description: string | null;
  barcode: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface ArticleInput {
  reference: string;
  designation: string;
  category_id: string;
  unit: string;
  main_supplier_id: string | null;
  purchase_price: string;
  sale_price: string;
  min_stock: string;
  max_stock: string | null;
  description: string;
  barcode: string;
}

export const catalogKeys = {
  categories: ['catalog', 'categories'] as const,
  articles: ['catalog', 'articles'] as const,
};

export function useCategories(query: string) {
  return useQuery({
    queryKey: [...catalogKeys.categories, query],
    queryFn: ({ signal }) => api.get<Page<Category>>(`/catalog/categories?${query}`, signal),
    placeholderData: keepPreviousData,
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
