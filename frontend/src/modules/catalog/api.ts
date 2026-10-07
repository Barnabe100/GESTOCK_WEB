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
  /** Lot 3-G : stock ventilé par lot (numéro obligatoire à la réception). */
  lot_tracked: boolean;
  /** Lot 3-G : date de péremption obligatoire sur le lot (suppose le suivi par lot). */
  expiry_tracked: boolean;
  /** Recette, étape 1 : état dans l'assortiment du site demandé (`site_id`), sinon absent. */
  site_assortment?: AssortmentState;
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
  lot_tracked?: boolean;
  expiry_tracked?: boolean;
  /** Création seulement : sites dont l'assortiment reçoit l'article (facultatif, D6). */
  site_ids?: string[];
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

/**
 * Code-barres d'une présentation (Lot 3-D) : `PRIMARY` (code principal = champ `barcode` de
 * l'article, modifié sur l'article), `ADDITIONAL` (code supplémentaire, unité de base) ou
 * `PACKAGING` (code d'un conditionnement). `is_active` : porté par un élément actif — un élément
 * désactivé libère ses codes, qui restent affichés.
 */
export interface Barcode {
  id: string;
  article_id: string;
  packaging_id: string | null;
  packaging_name: string | null;
  code: string;
  kind: 'PRIMARY' | 'ADDITIONAL' | 'PACKAGING';
  is_active: boolean;
  created_at: string;
}

/** Présentation identifiée par un scan exact : l'article, et le conditionnement s'il y a lieu. */
export interface ScanResult {
  article: Article;
  packaging: Packaging | null;
}

export const catalogKeys = {
  categories: ['catalog', 'categories'] as const,
  articles: ['catalog', 'articles'] as const,
  packagings: ['catalog', 'packagings'] as const,
  barcodes: ['catalog', 'barcodes'] as const,
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

/**
 * Lot 3-G (ADR-0045, P1-b) : le suivi par lot est-il activable ? Faux tant que la consommation
 * des lots (Lot 3-H) n'est pas livrée — l'interface ne propose pas l'activation et le serveur
 * la refuse de toute façon (`lot_tracking_unavailable`).
 */
export function useLotTracking(enabled = true) {
  return useQuery({
    queryKey: ['catalog', 'lot-tracking'],
    queryFn: ({ signal }) => api.get<{ available: boolean }>('/catalog/lot-tracking', signal),
    staleTime: Infinity,
    enabled,
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
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: catalogKeys.articles });
      void qc.invalidateQueries({ queryKey: catalogKeys.barcodes });
    },
  });
}

export function useSetArticleActive() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, active }: { id: string; active: boolean }) =>
      api.post<Article>(`/catalog/articles/${id}/${active ? 'activate' : 'deactivate'}`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: catalogKeys.articles });
      // Lot 3-D : les codes suivent l'état de l'article.
      void qc.invalidateQueries({ queryKey: catalogKeys.barcodes });
    },
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
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: catalogKeys.packagings });
      void qc.invalidateQueries({ queryKey: catalogKeys.barcodes });
    },
  });
}

/**
 * Scan des écrans opérationnels (Lot 3-D) : égalité EXACTE côté serveur parmi les présentations
 * actives — toujours la valeur saisie à l'instant de la validation, jamais un résultat affiché.
 * `404 barcode_unknown` si aucune présentation ne porte exactement ce code.
 */
export function resolveBarcode(code: string) {
  return api.get<ScanResult>(
    `/catalog/barcodes/resolve?${new URLSearchParams({ code }).toString()}`,
  );
}

export function useBarcodes(articleId: string) {
  return useQuery({
    queryKey: [...catalogKeys.barcodes, articleId],
    queryFn: ({ signal }) =>
      api.get<Page<Barcode>>(`/catalog/articles/${articleId}/barcodes?limit=200`, signal),
  });
}

export function useAddBarcode() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      articleId,
      packagingId,
      code,
    }: {
      articleId: string;
      packagingId: string | null;
      code: string;
    }) =>
      packagingId
        ? api.post<Barcode>(`/catalog/packagings/${packagingId}/barcodes`, { code })
        : api.post<Barcode>(`/catalog/articles/${articleId}/barcodes`, { code }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: catalogKeys.barcodes }),
  });
}

export function useRemoveBarcode() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`/catalog/barcodes/${id}`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: catalogKeys.barcodes }),
  });
}

// --- Assortiment par site (Recette, étape 1, ADR-0046) -----------------------------------------
// CATALOGUE TENANT ≠ ASSORTIMENT SITE ≠ STOCK SITE. Le serveur décide (permission par site,
// abonnement du site, blocages du retrait) ; l'interface ne fait que guider.

export const ASSORTMENT_MANAGE = 'catalog.assortment.manage';

/** `active` : proposé par le site ; `removed` : retiré (réactivable) ; `none` : jamais associé. */
export type AssortmentState = 'active' | 'removed' | 'none';
export type AssortmentStatusFilter = 'active' | 'removed' | 'all';

export interface SiteArticle {
  article_id: string;
  reference: string;
  designation: string;
  category_id: string;
  category_name: string;
  unit: string;
  article_active: boolean;
  stock_managed: boolean;
  state: AssortmentState;
  added_at: string;
  added_by_name: string | null;
  removed_at: string | null;
  removed_by_name: string | null;
}

export interface ArticleSite {
  site_id: string;
  site_name: string;
  state: AssortmentState;
  added_at: string | null;
  removed_at: string | null;
}

export interface AssortmentChange {
  added: number;
  reactivated: number;
  unchanged: number;
}

export interface AssortmentRemoval {
  removed: number;
  unchanged: number;
}

/** Détail d'un retrait refusé (`409 article_has_stock` / `article_in_open_documents`). */
export interface RemovalBlocked {
  reference: string;
  reason: 'stock' | 'open_document';
  documents: string[];
}

export const assortmentKeys = {
  all: ['catalog', 'assortment'] as const,
};

export function useSiteArticles(siteId: string | null, query: string) {
  return useQuery({
    queryKey: [...assortmentKeys.all, 'site', siteId, query],
    queryFn: ({ signal }) =>
      api.get<Page<SiteArticle>>(`/catalog/sites/${siteId}/articles?${query}`, signal),
    placeholderData: keepPreviousData,
    enabled: siteId !== null,
  });
}

export function useArticleSites(articleId: string | undefined, enabled = true) {
  return useQuery({
    queryKey: [...assortmentKeys.all, 'article', articleId],
    queryFn: ({ signal }) => api.get<ArticleSite[]>(`/catalog/articles/${articleId}/sites`, signal),
    enabled: enabled && articleId !== undefined,
  });
}

/** Toute modification de l'assortiment change ce que les sites proposent : tout est relu. */
function useAssortmentInvalidation() {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: assortmentKeys.all });
    void qc.invalidateQueries({ queryKey: catalogKeys.articles });
    void qc.invalidateQueries({ queryKey: ['stock'] });
    void qc.invalidateQueries({ queryKey: ['pos'] });
    void qc.invalidateQueries({ queryKey: ['inventories'] });
    void qc.invalidateQueries({ queryKey: ['alerts'] });
  };
}

export function useAssortmentMutations() {
  const invalidate = useAssortmentInvalidation();
  return {
    add: useMutation({
      mutationFn: ({ siteId, articleIds }: { siteId: string; articleIds: string[] }) =>
        api.post<AssortmentChange>(`/catalog/sites/${siteId}/articles`, {
          article_ids: articleIds,
        }),
      onSuccess: invalidate,
    }),
    remove: useMutation({
      mutationFn: ({ siteId, articleIds }: { siteId: string; articleIds: string[] }) =>
        api.post<AssortmentRemoval>(`/catalog/sites/${siteId}/articles/remove`, {
          article_ids: articleIds,
        }),
      onSuccess: invalidate,
    }),
    copy: useMutation({
      mutationFn: ({
        siteId,
        sourceSiteId,
        categoryId,
      }: {
        siteId: string;
        sourceSiteId: string;
        categoryId: string | null;
      }) =>
        api.post<AssortmentChange>(`/catalog/sites/${siteId}/articles/copy`, {
          source_site_id: sourceSiteId,
          category_id: categoryId,
        }),
      onSuccess: invalidate,
    }),
  };
}
