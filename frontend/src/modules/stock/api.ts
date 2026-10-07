import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/core/api/client';
import type { Page } from '@/shared/lib/serverTable';

/** Montants, quantités et coûts : chaînes décimales calculées par le backend. */

export type LevelState = 'ok' | 'low' | 'out' | 'not_stocked';
export type LevelStateFilter = 'all' | 'alerts' | LevelState;

export interface StockLevel {
  site_id: string;
  site_name: string;
  article_id: string;
  reference: string;
  designation: string;
  unit: string;
  category_name: string;
  article_active: boolean;
  quantity: string;
  /** Coûts internes : absents sans `catalog.article.cost_view` (Lot 3-A). */
  average_cost?: string;
  stock_value?: string;
  min_stock: string;
  max_stock: string | null;
  min_override: string | null;
  max_override: string | null;
  state: LevelState;
  /** Lot 3-F : emplacement COURANT de l'article sur ce site (nul : non rangé). */
  location_id?: string | null;
  location_name?: string | null;
  location_active?: boolean | null;
  /**
   * Recette, étape 1 (ADR-0046) : `false` = stock restant d'un article retiré de l'assortiment
   * du site (« Hors assortiment ») — aucune opération sans réactivation, jamais une alerte.
   */
  in_assortment?: boolean;
}

export type MovementType =
  'ENTRY' | 'EXIT' | 'CANCELLATION' | 'ADJUSTMENT' | 'TRANSFER_OUT' | 'TRANSFER_IN' | 'SALE';

export interface Movement {
  id: string;
  occurred_at: string;
  site_id: string;
  site_name: string;
  article_id: string;
  article_reference: string;
  article_designation: string;
  unit: string;
  movement_type: MovementType;
  quantity: string;
  quantity_before: string;
  quantity_after: string;
  /** Coûts internes : absents sans `catalog.article.cost_view` (Lot 3-A). */
  unit_cost?: string | null;
  average_cost_before?: string;
  average_cost_after?: string;
  source_type: string;
  source_id: string;
  document_number: string | null;
  origin_movement_id: string | null;
  user_name: string | null;
  comment: string | null;
  /** Lot 3-C : présentation saisie (« 3 Carton 24 » pour −72) ; nulle en unité de base. */
  packaging_name?: string | null;
  packaging_conversion?: string | null;
  packaging_quantity?: string | null;
  /** Lot 3-G : lot du mouvement (réception, annulation de réception). */
  lot_id?: string | null;
  lot_number?: string | null;
  lot_expiry_date?: string | null;
}

export interface ExitReason {
  id: string;
  code: string | null;
  label: string;
  description: string | null;
  is_system: boolean;
  is_active: boolean;
}

export type DocumentStatus = 'DRAFT' | 'VALIDATED' | 'CANCELLED';
export type DocumentKind = 'entries' | 'exits';
export type EntryKind = 'PURCHASE' | 'INITIAL_STOCK';

export interface DocumentLine {
  id: string;
  line_no: number;
  article_id: string;
  article_reference: string;
  article_designation: string;
  unit: string;
  /** Quantité dans la présentation saisie (Lot 3-C) ; `base_quantity` = unité de base. */
  quantity: string;
  /** Coûts : absents sans `catalog.article.cost_view` (Lot 3-A). Entrée : coût par
   *  présentation saisie ; sortie / transfert : CMUP par unité de base. */
  unit_cost?: string | null;
  amount?: string | null;
  /** Lot 3-C : conditionnement saisi (instantané figé ; nul = unité de base). */
  packaging_id?: string | null;
  packaging_name?: string | null;
  packaging_conversion?: string | null;
  base_quantity?: string;
  /** Lot 3-F (entrées, sorties) : emplacement COURANT sur le site du document, indicatif. */
  location_name?: string | null;
  /** Lot 3-G (réceptions) : lot saisi ; `lot_id` résolu à la validation ; état calculé. */
  lot_id?: string | null;
  lot_number?: string | null;
  lot_expiry_date?: string | null;
  lot_manufacturing_date?: string | null;
  lot_state?: LotState | null;
  /** Lot 3-H-A (sorties) : choix du brouillon, ou répartition réelle d'une sortie validée
   *  (journal des mouvements). Quantités en unité de base. */
  lots?: LineLot[];
}

/** Lot d'une ligne de sortie (Lot 3-H-A) : quantité en unité de base. */
export interface LineLot {
  lot_id: string;
  lot_number: string;
  expiry_date?: string | null;
  state?: LotState | null;
  quantity: string;
}

/** Choix manuel d'un lot sur une ligne (quantité en unité de base). */
export interface LotAllocationInput {
  lot_id: string;
  quantity: string;
}

interface DocumentBase {
  id: string;
  number: string;
  site_id: string;
  site_name: string;
  status: DocumentStatus;
  operation_date: string;
  comment: string | null;
  total_amount?: string | null;
  line_count: number;
  created_at: string;
  created_by_name: string | null;
  validated_at: string | null;
  validated_by_name: string | null;
  cancelled_at: string | null;
  cancelled_by_name: string | null;
  cancellation_reason: string | null;
  lines: DocumentLine[];
}

export interface StockEntry extends DocumentBase {
  kind: EntryKind;
  supplier_id: string | null;
  supplier_name: string | null;
  document_reference: string | null;
}

export interface StockExit extends DocumentBase {
  reason_id: string;
  reason_label: string;
  beneficiary: string | null;
  reference: string | null;
}

export type StockDocument = StockEntry | StockExit;

/** Préfixe des permissions et des libellés selon le type de document. */
export const DOCUMENT_CONFIG = {
  entries: { permission: 'stock.entry', i18n: 'entries' },
  exits: { permission: 'stock.exit', i18n: 'exits' },
} as const;

export interface EntryInput {
  site_id?: string | null;
  kind: EntryKind;
  operation_date: string | null;
  supplier_id: string | null;
  document_reference: string | null;
  comment: string | null;
  /** `packaging_id` nul : unité de base ; quantité et coût dans la présentation saisie. */
  lines: EntryLineInput[];
}

export interface EntryLineInput {
  article_id: string;
  packaging_id: string | null;
  quantity: string;
  unit_cost: string;
  /** Lot 3-G : article suivi par lot seulement (contrôle serveur). */
  lot_number?: string | null;
  lot_expiry_date?: string | null;
  lot_manufacturing_date?: string | null;
}

export interface ExitInput {
  site_id?: string | null;
  operation_date: string | null;
  reason_id: string;
  beneficiary: string | null;
  reference: string | null;
  comment: string | null;
  /** `lots` (Lot 3-H-A) : répartition manuelle d'un article suivi par lot — incomplète
   *  possible dans un brouillon, somme exacte exigée par le serveur à la validation. */
  lines: {
    article_id: string;
    packaging_id: string | null;
    quantity: string;
    lots?: LotAllocationInput[];
  }[];
}

// --- Lots et péremption (Lot 3-G, ADR-0045) -----------------------------------------------------

/** État de péremption CALCULÉ par le serveur (fuseau et seuil du tenant). */
export type LotState = 'no_expiry' | 'ok' | 'expiring_soon' | 'expired';
export type LotStateFilter = 'all' | LotState;

/** Lot et son solde (unité de base) sur les sites visibles — aucun coût (CMUP du site). */
export interface StockLot {
  id: string;
  article_id: string;
  article_reference: string;
  article_designation: string;
  unit: string;
  number: string;
  expiry_date: string | null;
  manufacturing_date: string | null;
  state: LotState;
  quantity: string;
  site_count: number;
  created_at: string;
}

export interface StockLotDetail extends StockLot {
  balances: { site_id: string; site_name: string; quantity: string }[];
}

export const stockKeys = {
  all: ['stock'] as const,
  levels: ['stock', 'levels'] as const,
  movements: ['stock', 'movements'] as const,
  reasons: ['stock', 'exit-reasons'] as const,
  documents: (kind: DocumentKind) => ['stock', kind] as const,
};

// --- Niveaux et seuils --------------------------------------------------------------------------

export function useStockLevels(query: string, enabled = true) {
  return useQuery({
    queryKey: [...stockKeys.levels, query],
    queryFn: ({ signal }) => api.get<Page<StockLevel>>(`/stock/levels?${query}`, signal),
    placeholderData: keepPreviousData,
    enabled,
  });
}

export function useSetThresholds() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      siteId,
      articleId,
      min_stock,
      max_stock,
    }: {
      siteId: string;
      articleId: string;
      min_stock: string | null;
      max_stock: string | null;
    }) =>
      api.put<StockLevel>(`/stock/levels/${siteId}/${articleId}/thresholds`, {
        min_stock,
        max_stock,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: stockKeys.levels });
      void qc.invalidateQueries({ queryKey: ['alerts'] });
    },
  });
}

// --- Emplacements physiques par site (Lot 3-F) -------------------------------------------------

export const LOCATION_MANAGE = 'stock.location.manage';

export interface StockLocation {
  id: string;
  site_id: string;
  site_name: string;
  name: string;
  is_active: boolean;
  /** Articles dont c'est l'emplacement courant sur ce site. */
  article_count: number;
  created_at: string;
  updated_at: string;
}

export const locationKeys = { all: ['stock', 'locations'] as const };

export function useStockLocations(query: string, enabled = true) {
  return useQuery({
    queryKey: [...locationKeys.all, query],
    queryFn: ({ signal }) => api.get<Page<StockLocation>>(`/stock/locations?${query}`, signal),
    placeholderData: keepPreviousData,
    enabled,
  });
}

/** Création, renommage, activation : rafraîchit emplacements et niveaux (noms affichés). */
export function useLocationMutations() {
  const qc = useQueryClient();
  const onSuccess = () => {
    void qc.invalidateQueries({ queryKey: locationKeys.all });
    void qc.invalidateQueries({ queryKey: stockKeys.levels });
  };
  return {
    create: useMutation({
      mutationFn: (input: { site_id: string | null; name: string }) =>
        api.post<StockLocation>('/stock/locations', input),
      onSuccess,
    }),
    rename: useMutation({
      mutationFn: ({ id, name }: { id: string; name: string }) =>
        api.patch<StockLocation>(`/stock/locations/${id}`, { name }),
      onSuccess,
    }),
    setActive: useMutation({
      mutationFn: ({ id, active }: { id: string; active: boolean }) =>
        api.post<StockLocation>(`/stock/locations/${id}/${active ? 'activate' : 'deactivate'}`),
      onSuccess,
    }),
  };
}

/** Emplacement courant d'un article sur un site (`null` : non rangé). */
export function useAssignLocation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      siteId,
      articleId,
      locationId,
    }: {
      siteId: string;
      articleId: string;
      locationId: string | null;
    }) =>
      api.put<StockLevel>(`/stock/levels/${siteId}/${articleId}/location`, {
        location_id: locationId,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: stockKeys.all });
      void qc.invalidateQueries({ queryKey: ['inventories'] });
    },
  });
}

// --- Lots et seuil de péremption (Lot 3-G) ------------------------------------------------------

export const lotKeys = {
  all: ['stock', 'lots'] as const,
  settings: ['stock', 'settings'] as const,
};

export function useLots(query: string, enabled = true) {
  return useQuery({
    queryKey: [...lotKeys.all, query],
    queryFn: ({ signal }) => api.get<Page<StockLot>>(`/stock/lots?${query}`, signal),
    placeholderData: keepPreviousData,
    enabled,
  });
}

export function useLot(id: string | undefined) {
  return useQuery({
    queryKey: [...lotKeys.all, 'detail', id],
    queryFn: ({ signal }) => api.get<StockLotDetail>(`/stock/lots/${id}`, signal),
    enabled: id !== undefined,
  });
}

/** Seuil « bientôt périmé » du tenant, en jours (défaut serveur : 30). */
export function useStockSettings(enabled = true) {
  return useQuery({
    queryKey: lotKeys.settings,
    queryFn: ({ signal }) => api.get<{ expiry_warning_days: number }>('/stock/settings', signal),
    enabled,
  });
}

export function useSaveStockSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (expiry_warning_days: number) =>
      api.put<{ expiry_warning_days: number }>('/stock/settings', { expiry_warning_days }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['stock'] }),
  });
}

// --- Mouvements ---------------------------------------------------------------------------------

export function useMovements(query: string) {
  return useQuery({
    queryKey: [...stockKeys.movements, query],
    queryFn: ({ signal }) => api.get<Page<Movement>>(`/stock/movements?${query}`, signal),
    placeholderData: keepPreviousData,
  });
}

// --- Motifs de sortie ---------------------------------------------------------------------------

export function useExitReasons(query: string, enabled = true) {
  return useQuery({
    queryKey: [...stockKeys.reasons, query],
    queryFn: ({ signal }) => api.get<Page<ExitReason>>(`/stock/exit-reasons?${query}`, signal),
    placeholderData: keepPreviousData,
    enabled,
  });
}

export function useSaveExitReason() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      input,
    }: {
      id?: string;
      input: { label: string; description: string | null };
    }) =>
      id
        ? api.patch<ExitReason>(`/stock/exit-reasons/${id}`, input)
        : api.post<ExitReason>('/stock/exit-reasons', input),
    onSuccess: () => void qc.invalidateQueries({ queryKey: stockKeys.reasons }),
  });
}

export function useSetExitReasonActive() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, active }: { id: string; active: boolean }) =>
      api.post<ExitReason>(`/stock/exit-reasons/${id}/${active ? 'activate' : 'deactivate'}`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: stockKeys.reasons }),
  });
}

// --- Documents (entrées / sorties) --------------------------------------------------------------

export function useDocuments<T extends StockDocument>(kind: DocumentKind, query: string) {
  return useQuery({
    queryKey: [...stockKeys.documents(kind), 'list', query],
    queryFn: ({ signal }) => api.get<Page<T>>(`/stock/${kind}?${query}`, signal),
    placeholderData: keepPreviousData,
  });
}

export function useDocument<T extends StockDocument>(kind: DocumentKind, id: string | undefined) {
  return useQuery({
    queryKey: [...stockKeys.documents(kind), 'detail', id],
    queryFn: ({ signal }) => api.get<T>(`/stock/${kind}/${id}`, signal),
    enabled: id !== undefined,
  });
}

/** Opérations sur un document ; tout changement invalide le stock (niveaux, mouvements…). */
export function useDocumentMutations<T extends StockDocument>(kind: DocumentKind) {
  const qc = useQueryClient();
  const onSuccess = (document: T) => {
    qc.setQueryData([...stockKeys.documents(kind), 'detail', document.id], document);
    void qc.invalidateQueries({ queryKey: stockKeys.all });
    void qc.invalidateQueries({ queryKey: ['alerts'] });
  };
  return {
    save: useMutation({
      mutationFn: ({ id, input }: { id?: string; input: EntryInput | ExitInput }) =>
        id ? api.put<T>(`/stock/${kind}/${id}`, input) : api.post<T>(`/stock/${kind}`, input),
      onSuccess,
    }),
    validate: useMutation({
      mutationFn: (id: string) => api.post<T>(`/stock/${kind}/${id}/validate`),
      onSuccess,
    }),
    cancel: useMutation({
      mutationFn: ({ id, reason }: { id: string; reason: string }) =>
        api.post<T>(`/stock/${kind}/${id}/cancel`, { reason }),
      onSuccess,
    }),
  };
}

// --- Fiche fournisseur (Lot 3-E) : réceptions VALIDÉES des sites visibles, calculées par le
// serveur. Coûts (`received_total`, `last_unit_cost`) absents des réponses sans cost_view.

export interface SupplierReceptionSummary {
  supplier_id: string;
  validated_count: number;
  last_received_on: string | null;
  received_total?: string;
}

export interface SupplierReceivedArticle {
  article_id: string;
  article_reference: string;
  article_designation: string;
  unit: string;
  article_active: boolean;
  receipt_count: number;
  /** Quantité reçue en unité de base. */
  received_base_quantity: string;
  last_received_on: string;
  last_entry_id: string;
  last_entry_number: string;
  /** Coût par unité de base de la dernière réception validée. */
  last_unit_cost?: string;
}

export function useSupplierReceptionSummary(supplierId: string, enabled: boolean) {
  return useQuery({
    queryKey: [...stockKeys.all, 'supplier', supplierId, 'summary'],
    queryFn: ({ signal }) =>
      api.get<SupplierReceptionSummary>(`/stock/suppliers/${supplierId}/summary`, signal),
    enabled,
  });
}

export function useSupplierReceivedArticles(supplierId: string, query: string) {
  return useQuery({
    queryKey: [...stockKeys.all, 'supplier', supplierId, 'articles', query],
    queryFn: ({ signal }) =>
      api.get<Page<SupplierReceivedArticle>>(
        `/stock/suppliers/${supplierId}/articles?${query}`,
        signal,
      ),
    placeholderData: keepPreviousData,
  });
}

// --- Lots disponibles (Lot 3-H-A, H-D18) ---------------------------------------------------------

/** Lot ayant un solde positif sur le site : ordre de consommation du serveur (FEFO / FIFO),
 *  lots périmés en dernier (`expired` : jamais consommés automatiquement). Aucun coût. */
export interface AvailableLot {
  lot_id: string;
  number: string;
  quantity: string;
  expiry_date: string | null;
  manufacturing_date: string | null;
  state: LotState;
  expired: boolean;
  created_at: string;
}

export interface AvailableLots {
  article_id: string;
  lot_tracked: boolean;
  expiry_tracked: boolean;
  lots: AvailableLot[];
}

/**
 * Lots disponibles d'un article sur un site. `path` : point d'accès propre à l'usage — sorties
 * (`/stock/available-lots`), transferts (`/stock/transfers/available-lots`), ventes
 * (`/sales/articles/{id}/lots`), point de vente (`/pos/articles/{id}/lots`) — chacun avec ses
 * permissions.
 */
export function useAvailableLots(path: string | null, enabled = true) {
  return useQuery({
    queryKey: [...lotKeys.all, 'available', path],
    queryFn: ({ signal }) => api.get<AvailableLots>(path ?? '', signal),
    enabled: enabled && path !== null,
  });
}

export function exitLotsPath(articleId: string, siteId: string): string {
  return `/stock/available-lots?${new URLSearchParams({ article_id: articleId, site_id: siteId }).toString()}`;
}

/** Lots du site SOURCE d'un transfert (Lot 3-H-B1) : point d'accès propre aux transferts
 *  (`stock.transfer.create`) ; lots périmés signalés, jamais transférables. */
export function transferLotsPath(articleId: string, siteId: string): string {
  return `/stock/transfers/available-lots?${new URLSearchParams({ article_id: articleId, site_id: siteId }).toString()}`;
}
