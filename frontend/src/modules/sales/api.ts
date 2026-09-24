import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/core/api/client';
import type { Page } from '@/shared/lib/serverTable';

export type SaleStatus = 'DRAFT' | 'VALIDATED' | 'CANCELLED';
export const SALE_STATUSES: readonly SaleStatus[] = ['DRAFT', 'VALIDATED', 'CANCELLED'];

/** Montants et quantités : chaînes décimales calculées par le serveur (qui fait foi). */
export interface SaleLine {
  id: string;
  line_no: number;
  article_id: string;
  article_reference: string;
  article_designation: string;
  unit: string;
  quantity: string;
  unit_price: string;
  line_total: string;
}

export interface Sale {
  id: string;
  number: string;
  site_id: string;
  site_name: string;
  customer_id: string | null;
  customer_code: string | null;
  customer_name: string | null;
  status: SaleStatus;
  sale_date: string;
  notes: string | null;
  subtotal: string;
  total: string;
  line_count: number;
  created_at: string;
  updated_at: string;
  created_by_name: string | null;
  validated_at: string | null;
  validated_by_name: string | null;
  cancelled_at: string | null;
  cancelled_by_name: string | null;
  cancellation_reason: string | null;
  lines: SaleLine[];
}

/** Aucun prix ni total : le serveur les lit dans le catalogue et les calcule. */
export interface SaleInput {
  site_id?: string | null;
  sale_date: string | null;
  customer_id: string | null;
  notes: string | null;
  lines: { article_id: string; quantity: string }[];
}

export const saleKeys = { all: ['sales'] as const };

export function useSales(query: string) {
  return useQuery({
    queryKey: [...saleKeys.all, 'list', query],
    queryFn: ({ signal }) => api.get<Page<Sale>>(`/sales?${query}`, signal),
    placeholderData: keepPreviousData,
  });
}

export function useSale(id: string | undefined) {
  return useQuery({
    queryKey: [...saleKeys.all, 'detail', id],
    queryFn: ({ signal }) => api.get<Sale>(`/sales/${id}`, signal),
    enabled: id !== undefined,
  });
}

/** Toute opération peut modifier le stock : ventes, niveaux, mouvements et alertes rechargés. */
export function useSaleMutations() {
  const qc = useQueryClient();
  const onSuccess = (sale: Sale) => {
    qc.setQueryData([...saleKeys.all, 'detail', sale.id], sale);
    void qc.invalidateQueries({ queryKey: saleKeys.all });
    void qc.invalidateQueries({ queryKey: ['stock'] });
    void qc.invalidateQueries({ queryKey: ['alerts'] });
  };
  return {
    save: useMutation({
      mutationFn: ({ id, input }: { id?: string; input: SaleInput }) =>
        id ? api.put<Sale>(`/sales/${id}`, input) : api.post<Sale>('/sales', input),
      onSuccess,
    }),
    validate: useMutation({
      mutationFn: (id: string) => api.post<Sale>(`/sales/${id}/validate`),
      onSuccess,
    }),
    cancel: useMutation({
      mutationFn: ({ id, reason }: { id: string; reason: string }) =>
        api.post<Sale>(`/sales/${id}/cancel`, { reason }),
      onSuccess,
    }),
  };
}
