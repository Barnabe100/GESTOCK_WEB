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

/** Situation d'une vente à crédit, calculée par le serveur à partir des paiements. */
export type CreditStatus = 'OPEN' | 'PARTIAL' | 'PAID' | 'CANCELLED';

export interface Sale {
  id: string;
  /** Nul pour un brouillon : `VENT-{SITE}-{ANNÉE}-{SÉQUENCE}` attribué à la validation. */
  number: string | null;
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
  /** Vente à crédit : reste dû à la validation (client identifié obligatoire). */
  is_credit: boolean;
  credit_status: CreditStatus | null;
  /** Dépassement autorisé de la limite de crédit : autorisateur, date, motif, montant. */
  credit_override_at: string | null;
  credit_override_by_name: string | null;
  credit_override_reason: string | null;
  credit_override_amount: string | null;
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
export type ImmediatePayment = Pick<
  PaymentInput,
  'payment_method_id' | 'amount' | 'amount_received' | 'reference' | 'cash_register_id'
>;

/** Dépassement exceptionnel de la limite de crédit : justification (5 caractères au moins). */
export interface CreditOverride {
  reason: string;
}

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
      mutationFn: ({
        id,
        payments = [],
        creditOverride = null,
      }: {
        id: string;
        payments?: ImmediatePayment[];
        creditOverride?: CreditOverride | null;
      }) =>
        api.post<Sale>(
          `/sales/${id}/validate`,
          payments.length > 0 || creditOverride
            ? { payments, credit_override: creditOverride }
            : undefined,
        ),
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

/** Type d'un moyen de paiement : seul il gouverne le comportement (jamais le libellé). */
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
  sale_number: string | null;
  site_id: string;
  /** Montant imputé sur la vente. */
  amount: string;
  /** Instantané du moyen au moment du paiement (type, identifiant, libellé). */
  method: PaymentMethod;
  payment_method_id: string | null;
  method_label: string;
  provider: string | null;
  /** Espèces : montant remis par le client et monnaie rendue, calculée par le serveur. */
  amount_received: string | null;
  change_given: string | null;
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

/**
 * Aucun solde envoyé : le serveur le recalcule et refuse tout surpaiement. Espèces :
 * `amount_received` (montant remis) seul suffit — montant imputé = min(reçu, reste dû) — et la
 * monnaie est calculée par le serveur.
 */
export interface PaymentInput {
  payment_method_id: string;
  amount: string | null;
  amount_received?: string | null;
  provider?: string | null;
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

// --- Moyens de paiement configurables (Lot 1) --------------------------------------------------

export type PaymentIntegration = 'MANUAL' | 'API';

/** Moyen configuré par l'entreprise (« Espèces », « Orange Money »…). */
export interface ConfiguredPaymentMethod {
  id: string;
  label: string;
  kind: PaymentMethod;
  integration_mode: PaymentIntegration;
  reference_required: boolean;
  is_active: boolean;
  sort_order: number;
  /** Sites où le moyen est désactivé (disponible partout ailleurs s'il est actif). */
  disabled_site_ids: string[];
  /** Avec un site demandé : utilisable sur ce site. */
  available: boolean | null;
}

export interface PaymentMethodInput {
  label: string;
  kind?: PaymentMethod;
  reference_required: boolean;
  sort_order: number;
}

export const paymentMethodKeys = { all: ['payment-methods'] as const };

/** Moyens configurés ; avec `siteId` : disponibilité sur ce site (calculée par le serveur). */
export function usePaymentMethods(siteId: string | null = null, enabled = true) {
  return useQuery({
    queryKey: [...paymentMethodKeys.all, siteId],
    queryFn: ({ signal }) =>
      api.get<ConfiguredPaymentMethod[]>(
        siteId ? `/payment-methods?site_id=${siteId}` : '/payment-methods',
        signal,
      ),
    enabled,
  });
}

/** Moyens utilisables sur un site : actifs et non désactivés pour ce site. */
export function useAvailablePaymentMethods(siteId: string | null, enabled = true) {
  const query = usePaymentMethods(siteId, enabled && siteId !== null);
  return { ...query, methods: (query.data ?? []).filter((m) => m.available === true) };
}

export function usePaymentMethodMutations() {
  const qc = useQueryClient();
  const onSuccess = () => void qc.invalidateQueries({ queryKey: paymentMethodKeys.all });
  return {
    save: useMutation({
      mutationFn: ({ id, input }: { id?: string; input: PaymentMethodInput }) =>
        id
          ? api.patch<ConfiguredPaymentMethod>(`/payment-methods/${id}`, {
              label: input.label,
              reference_required: input.reference_required,
              sort_order: input.sort_order,
            })
          : api.post<ConfiguredPaymentMethod>('/payment-methods', input),
      onSuccess,
    }),
    setActive: useMutation({
      mutationFn: ({ id, active }: { id: string; active: boolean }) =>
        api.patch<ConfiguredPaymentMethod>(`/payment-methods/${id}`, { is_active: active }),
      onSuccess,
    }),
    setSite: useMutation({
      mutationFn: ({ id, siteId, enabled }: { id: string; siteId: string; enabled: boolean }) =>
        api.put<ConfiguredPaymentMethod>(`/payment-methods/${id}/sites/${siteId}`, { enabled }),
      onSuccess,
    }),
  };
}
