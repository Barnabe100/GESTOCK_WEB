import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/core/api/client';
import type { Payment, Sale } from '@/modules/sales/api';
import type { Page } from '@/shared/lib/serverTable';

/** Article du point de vente : prix du catalogue et stock du site (indicatifs). */
export interface PosArticle {
  article_id: string;
  reference: string;
  designation: string;
  unit: string;
  category_name: string | null;
  sale_price: string;
  quantity: string;
  is_active: boolean;
}

/**
 * Encaissement en une étape : le serveur crée, valide et encaisse la vente dans une seule
 * transaction (prix, totaux, stock, crédit, caisse recalculés et contrôlés côté serveur).
 */
export interface CheckoutInput {
  site_id: string;
  customer_id: string | null;
  lines: { article_id: string; quantity: string }[];
  /** Espèces : montant reçu (monnaie calculée par le serveur) ; autres moyens : montant payé. */
  payments: {
    payment_method_id: string;
    amount: string | null;
    amount_received: string | null;
    reference: string | null;
    cash_register_id: string | null;
  }[];
  /** Dépassement de la limite de crédit autorisé (si le serveur le permet à l'utilisateur). */
  credit_override?: { reason: string } | null;
  /** Une clé par panier : une double soumission renvoie la même vente. */
  idempotency_key: string;
}

export interface CheckoutResult {
  sale: Sale;
  payments: Payment[];
  replayed: boolean;
}

export const posKeys = { all: ['pos'] as const };

export function usePosArticles(siteId: string | null, search: string) {
  return useQuery({
    queryKey: [...posKeys.all, 'articles', siteId, search],
    queryFn: ({ signal }) =>
      api.get<PosArticle[]>(
        `/pos/articles?${new URLSearchParams({ site_id: siteId ?? '', search, limit: '24' }).toString()}`,
        signal,
      ),
    enabled: siteId !== null,
    placeholderData: keepPreviousData,
  });
}

/** Ventes récentes du point de vente sur le site (API Ventes existante, canal POS). */
export function useRecentPosSales(siteId: string | null, enabled: boolean) {
  return useQuery({
    queryKey: ['sales', 'pos-recent', siteId],
    queryFn: ({ signal }) =>
      api.get<Page<Sale>>(
        `/sales?${new URLSearchParams({
          site_id: siteId ?? '',
          channel: 'POS',
          sort: '-created_at',
          limit: '10',
        }).toString()}`,
        signal,
      ),
    enabled: enabled && siteId !== null,
  });
}

/** Après un encaissement : stock, ventes, créances, caisse et articles du POS rechargés. */
export function useCheckout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: CheckoutInput) => api.post<CheckoutResult>('/pos/checkout', input),
    onSuccess: () => {
      for (const key of ['pos', 'sales', 'stock', 'alerts', 'receivables', 'cash']) {
        void qc.invalidateQueries({ queryKey: [key] });
      }
    },
  });
}
