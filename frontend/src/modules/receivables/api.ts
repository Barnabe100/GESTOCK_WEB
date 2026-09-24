import { keepPreviousData, useQuery } from '@tanstack/react-query';

import { api } from '@/core/api/client';
import type { Payment, SalePaymentStatus } from '@/modules/sales/api';
import type { Page } from '@/shared/lib/serverTable';

/**
 * Créance ouverte : vente validée dont le reste dû (total − paiements effectués) est positif.
 * Tout est calculé par le serveur (aucune table de créances, aucun calcul faisant foi ici).
 */
export interface Receivable {
  sale_id: string;
  sale_number: string;
  sale_date: string;
  validated_at: string | null;
  site_id: string;
  site_name: string;
  /** Nul : vente sans client (reste dû sans débiteur identifié). */
  customer_id: string | null;
  customer_code: string | null;
  customer_name: string | null;
  customer_is_active: boolean | null;
  total: string;
  paid_amount: string;
  remaining_amount: string;
  payment_status: SalePaymentStatus;
}

/** États d'encaissement possibles d'une créance ouverte (une vente payée n'en est plus une). */
export type ReceivableStatus = 'UNPAID' | 'PARTIALLY_PAID';
export const RECEIVABLE_STATUSES: readonly ReceivableStatus[] = ['UNPAID', 'PARTIALLY_PAID'];

export interface ReceivableSummary {
  total_receivables: string;
  receivables_count: number;
  debtor_customers_count: number;
}

export interface ReceivableDetail extends Receivable {
  is_open: boolean;
  payments: Payment[];
}

/** Exposition crédit d'un client (voir ADR-0021). */
export interface CreditExposure {
  customer_id: string;
  customer_code: string;
  customer_name: string;
  customer_is_active: boolean;
  /** Nul : limite non configurée (aucune limite) — jamais un montant disponible fabriqué. */
  credit_limit: string | null;
  limit_configured: boolean;
  current_exposure: string;
  /** Nul : limite non configurée, ou vue limitée à certains sites (non consolidée). */
  available_credit: string | null;
  over_limit: boolean | null;
  open_receivables_count: number;
  /** Vrai : tous les sites du tenant ; faux : sites visibles du membre seulement. */
  consolidated: boolean;
}

export const receivableKeys = { all: ['receivables'] as const };

export function useReceivables(query: string) {
  return useQuery({
    queryKey: [...receivableKeys.all, 'list', query],
    queryFn: ({ signal }) => api.get<Page<Receivable>>(`/receivables?${query}`, signal),
    placeholderData: keepPreviousData,
  });
}

export function useReceivableSummary(query: string) {
  return useQuery({
    queryKey: [...receivableKeys.all, 'summary', query],
    queryFn: ({ signal }) => api.get<ReceivableSummary>(`/receivables/summary?${query}`, signal),
    placeholderData: keepPreviousData,
  });
}

export function useCustomerReceivables(customerId: string, query: string) {
  return useQuery({
    queryKey: [...receivableKeys.all, 'customer', customerId, query],
    queryFn: ({ signal }) =>
      api.get<Page<Receivable>>(`/customers/${customerId}/receivables?${query}`, signal),
    placeholderData: keepPreviousData,
  });
}

export function useCreditExposure(customerId: string) {
  return useQuery({
    queryKey: [...receivableKeys.all, 'exposure', customerId],
    queryFn: ({ signal }) =>
      api.get<CreditExposure>(`/customers/${customerId}/credit-exposure`, signal),
  });
}
