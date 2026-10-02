import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/core/api/client';
import type { ExpiredLotOverride, Payment, Sale } from '@/modules/sales/api';
import type { Page } from '@/shared/lib/serverTable';

/** Conditionnement ACTIF (Lot 3-B) : quantité de base = quantité × `conversion`. */
export interface PosPackaging {
  id: string;
  name: string;
  conversion: string;
  sale_price: string;
}

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
  /** `false` : vendu sans stock (quantité sans objet). */
  stock_managed: boolean;
  /** `false` : quantités entières seulement (contrôlé par le serveur). */
  decimal_quantity_allowed: boolean;
  /** Unité de base toujours vendable ; conditionnements actifs en plus. */
  packagings: PosPackaging[];
  /** Lot 3-D : présentation identifiée par un scan (code d'un conditionnement), sinon nul. */
  scanned_packaging_id?: string | null;
}

/**
 * Encaissement en une étape : le serveur crée, valide et encaisse la vente dans une seule
 * transaction (prix, totaux, stock, crédit, caisse recalculés et contrôlés côté serveur).
 */
export interface CheckoutInput {
  site_id: string;
  customer_id: string | null;
  /** `packaging_id` absent / nul : unité de base ; la quantité est dans cette présentation. */
  lines: { article_id: string; packaging_id: string | null; quantity: string }[];
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
  /** Lot 3-H-A (O-1) : dérogation explicite à la vente d'un lot périmé (motif, audit). */
  expired_lot_override?: ExpiredLotOverride | null;
  /** Une clé par panier : une double soumission renvoie la même vente. */
  idempotency_key: string;
}

export interface CheckoutResult {
  sale: Sale;
  payments: Payment[];
  replayed: boolean;
}

export const posKeys = { all: ['pos'] as const };

/**
 * Scan (Lot 3-A, Lot 3-D) : correspondance EXACTE d'un code (principal, supplémentaire ou de
 * conditionnement) d'une présentation active, côté serveur. Toujours la valeur saisie à
 * l'instant de la validation, jamais un résultat affiché. `404 barcode_unknown` si aucune
 * présentation ne porte exactement ce code ; `422 packaging_price_not_set` pour un
 * conditionnement au prix non configuré.
 */
export function findByBarcode(siteId: string, barcode: string) {
  return api.get<PosArticle>(
    `/pos/articles/by-barcode?${new URLSearchParams({ site_id: siteId, barcode }).toString()}`,
  );
}

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
