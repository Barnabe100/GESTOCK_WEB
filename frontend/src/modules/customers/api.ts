import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/core/api/client';
import type { Page } from '@/shared/lib/serverTable';

export type CustomerType = 'INDIVIDUAL' | 'BUSINESS';
export const CUSTOMER_TYPES: readonly CustomerType[] = ['INDIVIDUAL', 'BUSINESS'];

/** Client du tenant. Le code (CLI-000001) est attribué par le serveur. */
export interface Customer {
  id: string;
  code: string;
  customer_type: CustomerType;
  name: string;
  legal_name: string | null;
  tax_id: string | null;
  phone: string | null;
  phone2: string | null;
  email: string | null;
  address: string | null;
  city: string | null;
  country: string | null;
  notes: string | null;
  /** Montant en chaîne décimale (préparation des ventes à crédit). */
  credit_limit: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

/** Saisie : chaîne vide = champ effacé côté serveur. */
export interface CustomerInput {
  customer_type: CustomerType;
  name: string;
  legal_name: string;
  tax_id: string;
  phone: string;
  phone2: string;
  email: string;
  address: string;
  city: string;
  country: string;
  notes: string;
  credit_limit: string | null;
}

export const customerKeys = { all: ['customers'] as const };

export function useCustomers(query: string, enabled = true) {
  return useQuery({
    queryKey: [...customerKeys.all, 'list', query],
    queryFn: ({ signal }) => api.get<Page<Customer>>(`/customers?${query}`, signal),
    placeholderData: keepPreviousData,
    enabled,
  });
}

export function useCustomer(id: string | undefined) {
  return useQuery({
    queryKey: [...customerKeys.all, 'detail', id],
    queryFn: ({ signal }) => api.get<Customer>(`/customers/${id}`, signal),
    enabled: id !== undefined,
  });
}

export function useSaveCustomer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id?: string; input: CustomerInput }) =>
      id ? api.patch<Customer>(`/customers/${id}`, input) : api.post<Customer>('/customers', input),
    onSuccess: () => void qc.invalidateQueries({ queryKey: customerKeys.all }),
  });
}

export function useSetCustomerActive() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, active }: { id: string; active: boolean }) =>
      api.post<Customer>(`/customers/${id}/${active ? 'activate' : 'deactivate'}`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: customerKeys.all }),
  });
}
