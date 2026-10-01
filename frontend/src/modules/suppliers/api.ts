import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/core/api/client';
import type { Page } from '@/shared/lib/serverTable';

export interface Supplier {
  id: string;
  name: string;
  contact_name: string | null;
  phone: string | null;
  email: string | null;
  address: string | null;
  city: string | null;
  country: string | null;
  notes: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export type SupplierInput = Omit<Supplier, 'id' | 'is_active' | 'created_at' | 'updated_at'>;

export const supplierKeys = { all: ['suppliers'] as const };

export function useSuppliers(query: string, enabled = true) {
  return useQuery({
    queryKey: [...supplierKeys.all, 'list', query],
    queryFn: ({ signal }) => api.get<Page<Supplier>>(`/suppliers?${query}`, signal),
    placeholderData: keepPreviousData,
    enabled,
  });
}

export function useSupplier(id: string | undefined) {
  return useQuery({
    queryKey: [...supplierKeys.all, 'detail', id],
    queryFn: ({ signal }) => api.get<Supplier>(`/suppliers/${id}`, signal),
    enabled: id !== undefined,
  });
}

/** Évènement de la chronologie (Lot 3-E) : entrée réelle du journal d'audit. */
export interface SupplierEvent {
  id: string;
  occurred_at: string;
  action: string;
  user_name: string | null;
  data: Record<string, unknown>;
}

export function useSupplierHistory(id: string) {
  return useQuery({
    queryKey: [...supplierKeys.all, 'history', id],
    queryFn: ({ signal }) => api.get<SupplierEvent[]>(`/suppliers/${id}/history`, signal),
  });
}

export function useSaveSupplier() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id?: string; input: SupplierInput }) =>
      id ? api.patch<Supplier>(`/suppliers/${id}`, input) : api.post<Supplier>('/suppliers', input),
    onSuccess: () => void qc.invalidateQueries({ queryKey: supplierKeys.all }),
  });
}

export function useSetSupplierActive() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, active }: { id: string; active: boolean }) =>
      api.post<Supplier>(`/suppliers/${id}/${active ? 'activate' : 'deactivate'}`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: supplierKeys.all }),
  });
}
