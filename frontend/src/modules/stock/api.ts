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
  average_cost: string;
  stock_value: string;
  min_stock: string;
  max_stock: string | null;
  min_override: string | null;
  max_override: string | null;
  state: LevelState;
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
  unit_cost: string | null;
  average_cost_before: string;
  average_cost_after: string;
  source_type: string;
  source_id: string;
  document_number: string | null;
  origin_movement_id: string | null;
  user_name: string | null;
  comment: string | null;
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
  quantity: string;
  unit_cost: string | null;
  amount: string | null;
}

interface DocumentBase {
  id: string;
  number: string;
  site_id: string;
  site_name: string;
  status: DocumentStatus;
  operation_date: string;
  comment: string | null;
  total_amount: string | null;
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
  lines: { article_id: string; quantity: string; unit_cost: string }[];
}

export interface ExitInput {
  site_id?: string | null;
  operation_date: string | null;
  reason_id: string;
  beneficiary: string | null;
  reference: string | null;
  comment: string | null;
  lines: { article_id: string; quantity: string }[];
}

export const stockKeys = {
  all: ['stock'] as const,
  levels: ['stock', 'levels'] as const,
  movements: ['stock', 'movements'] as const,
  reasons: ['stock', 'exit-reasons'] as const,
  documents: (kind: DocumentKind) => ['stock', kind] as const,
};

// --- Niveaux et seuils --------------------------------------------------------------------------

export function useStockLevels(query: string) {
  return useQuery({
    queryKey: [...stockKeys.levels, query],
    queryFn: ({ signal }) => api.get<Page<StockLevel>>(`/stock/levels?${query}`, signal),
    placeholderData: keepPreviousData,
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
