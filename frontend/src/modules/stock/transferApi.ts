import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/core/api/client';
import type { Page } from '@/shared/lib/serverTable';

import { stockKeys, type DocumentLine, type DocumentStatus, type StockLevel } from './api';

/** Transfert inter-sites : coûts et montants figés par le serveur à la validation. */
export interface StockTransfer {
  id: string;
  number: string;
  source_site_id: string;
  source_site_name: string;
  destination_site_id: string;
  destination_site_name: string;
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

/** Aucun coût : le serveur lit le CMUP du site source à la validation. */
export interface TransferInput {
  source_site_id?: string | null;
  destination_site_id: string;
  operation_date: string | null;
  comment: string | null;
  lines: { article_id: string; quantity: string }[];
}

export const transferKeys = { all: ['stock', 'transfers'] as const };

export function useTransfers(query: string) {
  return useQuery({
    queryKey: [...transferKeys.all, 'list', query],
    queryFn: ({ signal }) => api.get<Page<StockTransfer>>(`/stock/transfers?${query}`, signal),
    placeholderData: keepPreviousData,
  });
}

export function useTransfer(id: string | undefined) {
  return useQuery({
    queryKey: [...transferKeys.all, 'detail', id],
    queryFn: ({ signal }) => api.get<StockTransfer>(`/stock/transfers/${id}`, signal),
    enabled: id !== undefined,
  });
}

/** Stock disponible (indicatif) des articles saisis sur le site source. */
export function useAvailableStock(siteId: string | null, articleIds: string[], enabled: boolean) {
  const ids = [...new Set(articleIds)].sort();
  // 200 = taille de page maximale de l'API (au-delà : affichage partiel, indicatif de toute façon).
  const params = new URLSearchParams({ limit: '200', include_inactive: 'true' });
  if (siteId) params.set('site_id', siteId);
  ids.forEach((id) => params.append('article_id', id));
  return useQuery({
    queryKey: [...stockKeys.levels, 'available', siteId, ids],
    queryFn: async ({ signal }) => {
      const page = await api.get<Page<StockLevel>>(`/stock/levels?${params.toString()}`, signal);
      return new Map(page.items.map((level) => [level.article_id, level.quantity]));
    },
    enabled: enabled && siteId !== null && ids.length > 0,
  });
}

/** Toute opération peut modifier le stock de deux sites : niveaux, mouvements, alertes. */
export function useTransferMutations() {
  const qc = useQueryClient();
  const onSuccess = (transfer: StockTransfer) => {
    qc.setQueryData([...transferKeys.all, 'detail', transfer.id], transfer);
    void qc.invalidateQueries({ queryKey: stockKeys.all });
    void qc.invalidateQueries({ queryKey: ['alerts'] });
  };
  return {
    save: useMutation({
      mutationFn: ({ id, input }: { id?: string; input: TransferInput }) =>
        id
          ? api.put<StockTransfer>(`/stock/transfers/${id}`, input)
          : api.post<StockTransfer>('/stock/transfers', input),
      onSuccess,
    }),
    validate: useMutation({
      mutationFn: (id: string) => api.post<StockTransfer>(`/stock/transfers/${id}/validate`),
      onSuccess,
    }),
    cancel: useMutation({
      mutationFn: ({ id, reason }: { id: string; reason: string }) =>
        api.post<StockTransfer>(`/stock/transfers/${id}/cancel`, { reason }),
      onSuccess,
    }),
  };
}

/** Fonctionnalité de plan des opérations de transfert (création, validation, annulation). */
export const TRANSFERS_FEATURE = 'stock.transfers';
