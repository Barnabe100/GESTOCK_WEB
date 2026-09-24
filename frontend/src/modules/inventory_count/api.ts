import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/core/api/client';
import { stockKeys } from '@/modules/stock/api';
import type { Page } from '@/shared/lib/serverTable';

export type InventoryStatus =
  'DRAFT' | 'COUNTING' | 'READY_TO_VALIDATE' | 'VALIDATED' | 'CANCELLED';
export type InventoryType = 'FULL' | 'TARGETED';
export type LineState = 'all' | 'counted' | 'uncounted' | 'surplus' | 'shortage' | 'no_variance';

export const INVENTORY_STATUSES: InventoryStatus[] = [
  'DRAFT',
  'COUNTING',
  'READY_TO_VALIDATE',
  'VALIDATED',
  'CANCELLED',
];
export const INVENTORY_TYPES: InventoryType[] = ['FULL', 'TARGETED'];

/** Résumé calculé par le serveur (avant validation : sur le stock courant ; après : figé). */
export interface InventorySummary {
  lines: number;
  counted: number;
  surplus: number;
  shortage: number;
  no_variance: number;
  surplus_value: string;
  shortage_value: string;
  adjustment_value: string;
  final: boolean;
}

export interface Inventory {
  id: string;
  number: string;
  site_id: string;
  site_name: string;
  status: InventoryStatus;
  inventory_type: InventoryType;
  comment: string | null;
  line_count: number;
  counted_count: number;
  variance_count: number;
  created_at: string;
  updated_at: string;
  created_by_name: string | null;
  started_at: string | null;
  started_by_name: string | null;
  completed_at: string | null;
  completed_by_name: string | null;
  validated_at: string | null;
  validated_by_name: string | null;
  cancelled_at: string | null;
  cancelled_by_name: string | null;
  cancellation_reason: string | null;
  summary: InventorySummary | null;
}

export interface InventoryLine {
  id: string;
  article_id: string;
  reference: string;
  designation: string;
  unit: string;
  category_name: string;
  article_active: boolean;
  stock_theoretical_initial: string;
  /** Stock courant du site (avant validation) : base de l'écart qui sera appliqué. */
  stock_current: string | null;
  stock_theoretical_at_validation: string | null;
  quantity_physical: string | null;
  /** Physique − théorique initial (information pendant le comptage). */
  indicative_variance: string | null;
  /** Physique − stock courant (figé à la validation). */
  quantity_variance: string | null;
  unit_cost: string | null;
  adjustment_value: string | null;
  counted_at: string | null;
  counted_by_name: string | null;
}

export interface Candidate {
  article_id: string;
  reference: string;
  designation: string;
  unit: string;
  category_name: string;
  stocked: boolean;
  quantity: string;
}

export interface InventoryCreateInput {
  site_id: string | null;
  inventory_type: InventoryType;
  article_ids: string[];
  comment: string | null;
}

export interface InventoryUpdateInput {
  comment: string | null;
  add_article_ids?: string[];
  remove_article_ids?: string[];
}

export const inventoryKeys = { all: ['inventories'] as const };

export function useInventories(query: string) {
  return useQuery({
    queryKey: [...inventoryKeys.all, 'list', query],
    queryFn: ({ signal }) => api.get<Page<Inventory>>(`/inventories?${query}`, signal),
    placeholderData: keepPreviousData,
  });
}

export function useInventory(id: string | undefined) {
  return useQuery({
    queryKey: [...inventoryKeys.all, 'detail', id],
    queryFn: ({ signal }) => api.get<Inventory>(`/inventories/${id}`, signal),
    enabled: id !== undefined,
  });
}

export function useInventoryLines(id: string, query: string) {
  return useQuery({
    queryKey: [...inventoryKeys.all, 'lines', id, query],
    queryFn: ({ signal }) =>
      api.get<Page<InventoryLine>>(`/inventories/${id}/lines?${query}`, signal),
    placeholderData: keepPreviousData,
  });
}

/** Articles proposables (recherche serveur, stock courant du site). */
export function useCandidates(query: string, enabled: boolean) {
  return useQuery({
    queryKey: [...inventoryKeys.all, 'candidates', query],
    queryFn: ({ signal }) => api.get<Page<Candidate>>(`/inventories/candidates?${query}`, signal),
    enabled,
    placeholderData: keepPreviousData,
  });
}

export function fetchCandidates(query: string) {
  return api.get<Page<Candidate>>(`/inventories/candidates?${query}`);
}

/** Après une opération : inventaire à jour dans le cache, listes et lignes rechargées. */
function useRefresh() {
  const qc = useQueryClient();
  return (inventory: Inventory) => {
    qc.setQueryData([...inventoryKeys.all, 'detail', inventory.id], inventory);
    void qc.invalidateQueries({ queryKey: inventoryKeys.all });
  };
}

/** Transition sans corps : start, complete-counting, reopen-counting. */
function useInventoryAction(action: string) {
  const refresh = useRefresh();
  return useMutation({
    mutationFn: (id: string) => api.post<Inventory>(`/inventories/${id}/${action}`),
    onSuccess: refresh,
  });
}

export function useInventoryMutations() {
  const qc = useQueryClient();
  const refresh = useRefresh();
  return {
    create: useMutation({
      mutationFn: (input: InventoryCreateInput) => api.post<Inventory>('/inventories', input),
      onSuccess: refresh,
    }),
    update: useMutation({
      mutationFn: ({ id, input }: { id: string; input: InventoryUpdateInput }) =>
        api.put<Inventory>(`/inventories/${id}`, input),
      onSuccess: refresh,
    }),
    start: useInventoryAction('start'),
    complete: useInventoryAction('complete-counting'),
    reopen: useInventoryAction('reopen-counting'),
    // La validation modifie le stock : niveaux, mouvements et alertes rechargés.
    validate: useMutation({
      mutationFn: (id: string) => api.post<Inventory>(`/inventories/${id}/validate`),
      onSuccess: (inventory: Inventory) => {
        refresh(inventory);
        void qc.invalidateQueries({ queryKey: stockKeys.all });
        void qc.invalidateQueries({ queryKey: ['alerts'] });
      },
    }),
    cancel: useMutation({
      mutationFn: ({ id, reason }: { id: string; reason: string }) =>
        api.post<Inventory>(`/inventories/${id}/cancel`, { reason }),
      onSuccess: refresh,
    }),
  };
}

/** Saisie progressive d'une quantité physique (null efface le comptage). */
export function useSaveCount(inventoryId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ lineId, quantity }: { lineId: string; quantity: string | null }) =>
      api.patch<{ lines: InventoryLine[]; summary: InventorySummary }>(
        `/inventories/${inventoryId}/lines`,
        { counts: [{ line_id: lineId, quantity_physical: quantity }] },
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: inventoryKeys.all });
    },
  });
}
