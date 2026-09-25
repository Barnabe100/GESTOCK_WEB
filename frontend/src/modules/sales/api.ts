import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/core/api/client';
import type { Page } from '@/shared/lib/serverTable';

export type SaleStatus = 'DRAFT' | 'VALIDATED' | 'CANCELLED';
/** État d'encaissement, indépendant du statut commercial de la vente. */
export type SalePaymentStatus = 'UNPAID' | 'PARTIALLY_PAID' | 'PAID';
export const SALE_PAYMENT_STATUSES: readonly SalePaymentStatus[] = [
  'UNPAID',
  'PARTIALLY_PAID',
  'PAID',
];
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
  /** Encaissement (vente validée seulement), calculé par le serveur. */
  paid_amount: string | null;
  remaining_amount: string | null;
  payment_status: SalePaymentStatus | null;
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

/** Encaissements immédiats à la validation (paiement comptant) : aucun solde envoyé. */
export type ImmediatePayment = Pick<PaymentInput, 'amount' | 'method' | 'cash_register_id'>;

/**
 * Toute opération peut modifier le stock et les créances : ventes, niveaux, mouvements,
 * alertes et créances rechargés.
 */
export function useSaleMutations() {
  const qc = useQueryClient();
  const onSuccess = (sale: Sale) => {
    qc.setQueryData([...saleKeys.all, 'detail', sale.id], sale);
    void qc.invalidateQueries({ queryKey: saleKeys.all });
    void qc.invalidateQueries({ queryKey: ['stock'] });
    void qc.invalidateQueries({ queryKey: ['alerts'] });
    void qc.invalidateQueries({ queryKey: ['receivables'] });
    void qc.invalidateQueries({ queryKey: ['cash'] });
  };
  return {
    save: useMutation({
      mutationFn: ({ id, input }: { id?: string; input: SaleInput }) =>
        id ? api.put<Sale>(`/sales/${id}`, input) : api.post<Sale>('/sales', input),
      onSuccess,
    }),
    validate: useMutation({
      mutationFn: ({ id, payments = [] }: { id: string; payments?: ImmediatePayment[] }) =>
        api.post<Sale>(`/sales/${id}/validate`, payments.length > 0 ? { payments } : undefined),
      onSuccess,
    }),
    cancel: useMutation({
      mutationFn: ({ id, reason }: { id: string; reason: string }) =>
        api.post<Sale>(`/sales/${id}/cancel`, { reason }),
      onSuccess,
    }),
  };
}

// --- Paiements (Phase 2.7) ---------------------------------------------------------------------

export type PaymentMethod = 'CASH' | 'MOBILE_MONEY' | 'CARD' | 'BANK_TRANSFER' | 'OTHER';
export const PAYMENT_METHODS: readonly PaymentMethod[] = [
  'CASH',
  'MOBILE_MONEY',
  'CARD',
  'BANK_TRANSFER',
  'OTHER',
];
export type PaymentStatus = 'PENDING' | 'COMPLETED' | 'CANCELLED';

export interface Payment {
  id: string;
  number: string;
  sale_id: string;
  sale_number: string;
  site_id: string;
  amount: string;
  method: PaymentMethod;
  provider: string | null;
  status: PaymentStatus;
  reference: string | null;
  paid_at: string;
  created_at: string;
  created_by_name: string | null;
  cancelled_at: string | null;
  cancelled_by_name: string | null;
  cancellation_reason: string | null;
}

export interface PaymentSummary {
  total: string;
  paid_amount: string;
  remaining_amount: string;
  payment_status: SalePaymentStatus;
}

export interface SalePayments {
  sale_id: string;
  sale_status: SaleStatus;
  summary: PaymentSummary | null;
  items: Payment[];
}

/** Aucun solde envoyé : le serveur le recalcule et refuse tout surpaiement. */
export interface PaymentInput {
  amount: string;
  method: PaymentMethod;
  provider: string | null;
  reference: string | null;
  /** Même clé pour une même saisie : une double soumission ne crée pas de doublon. */
  idempotency_key: string;
  /** Espèces : caisse du site de la vente (vérifiée par le serveur). */
  cash_register_id?: string | null;
}

export function useSalePayments(saleId: string, enabled: boolean) {
  return useQuery({
    queryKey: [...saleKeys.all, 'payments', saleId],
    queryFn: ({ signal }) => api.get<SalePayments>(`/sales/${saleId}/payments`, signal),
    enabled,
  });
}

/** Un encaissement ou son annulation change le solde : vente, historique et créances rechargés. */
export function usePaymentMutations(saleId: string) {
  const qc = useQueryClient();
  const onSuccess = () => {
    void qc.invalidateQueries({ queryKey: saleKeys.all });
    void qc.invalidateQueries({ queryKey: ['receivables'] });
    void qc.invalidateQueries({ queryKey: ['cash'] });
  };
  return {
    create: useMutation({
      mutationFn: (input: PaymentInput) => api.post<Payment>(`/sales/${saleId}/payments`, input),
      onSuccess,
    }),
    cancel: useMutation({
      mutationFn: ({ id, reason }: { id: string; reason: string }) =>
        api.post<Payment>(`/sales/${saleId}/payments/${id}/cancel`, { reason }),
      onSuccess,
    }),
  };
}
