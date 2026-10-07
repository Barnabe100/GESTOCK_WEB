import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/core/api/client';

import { saleKeys, type PaymentMethod, type SalePaymentStatus } from '../api';

/** Consultation : `sales.sale.view` (et sa portée) ; impressions : contrôlées par le serveur. */
export const RECEIPT_PRINT = 'sales.sale.receipt_print';
export const RECEIPT_REPRINT = 'sales.sale.reprint';

export interface ReceiptIdentityLine {
  kind: string;
  value: string;
}

export interface ReceiptLine {
  designation: string;
  unit: string;
  quantity: string;
  /** Présentation réellement vendue (instantané) ; nul : unité de base. */
  packaging_name: string | null;
  packaging_conversion: string | null;
  unit_price: string;
  line_total: string;
}

export interface ReceiptPayment {
  method: PaymentMethod;
  method_label: string;
  amount: string;
  amount_received: string | null;
  change_given: string | null;
  paid_at: string;
}

/** Reçu construit par le serveur à partir de la vente PERSISTÉE (jamais du panier). */
export interface Receipt {
  sale_id: string;
  number: string;
  site_name: string;
  issued_at: string;
  cashier_name: string | null;
  customer_name: string | null;
  issuer: {
    name: string;
    trade_name: string | null;
    logo_url: string | null;
    contact: ReceiptIdentityLine[];
    identifiers: ReceiptIdentityLine[];
  };
  lines: ReceiptLine[];
  total: string;
  paid_amount: string;
  remaining_amount: string;
  payment_status: SalePaymentStatus;
  is_credit: boolean;
  /** `null` sans `sales.payment.view` : ni détail, ni montant reçu, ni monnaie rendue. */
  payments: ReceiptPayment[] | null;
  amount_received: string | null;
  change_given: string | null;
  /** Impressions déjà journalisées : 0 = la prochaine est la première impression. */
  print_count: number;
}

const receiptKey = (saleId: string) => [...saleKeys.all, 'receipt', saleId] as const;

export function useSaleReceipt(saleId: string | null) {
  return useQuery({
    queryKey: receiptKey(saleId ?? ''),
    queryFn: ({ signal }) => api.get<Receipt>(`/sales/${saleId}/receipt`, signal),
    enabled: saleId !== null,
  });
}

/**
 * Impression autorisée et journalisée par le serveur (première impression : `receipt_print`,
 * suivantes : `reprint`) ; renvoie le reçu à imprimer.
 */
export function usePrintReceipt() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (saleId: string) => api.post<Receipt>(`/sales/${saleId}/receipt/print`),
    onSuccess: (receipt) => {
      qc.setQueryData(receiptKey(receipt.sale_id), receipt);
      // Chronologie de la vente : l'impression y est journalisée.
      void qc.invalidateQueries({ queryKey: [...saleKeys.all, 'history', receipt.sale_id] });
    },
  });
}
