import { compareMoney, percentOfMoney, subtractMoney } from '@/shared/lib/decimal';

/** Permission de la remise globale (palier R9, ADR-0054) : vérifiée par le serveur par site. */
export const SALE_DISCOUNT_PERMISSION = 'sales.sale.discount';

export type DiscountMode = 'AMOUNT' | 'PERCENT';

/** Remise saisie : montant ou pourcentage, motif obligatoire (le serveur calcule le montant). */
export interface SaleDiscount {
  mode: DiscountMode;
  value: string;
  reason: string;
}

/**
 * Montant INDICATIF de la remise (affichage en temps réel) : pourcentage du sous-total arrondi
 * au centime, borné au sous-total. Le serveur recalcule et refuse une remise trop grande.
 */
export function discountAmount(subtotal: string, discount: SaleDiscount | null): string {
  if (!discount) return '0.00';
  const amount =
    discount.mode === 'PERCENT'
      ? percentOfMoney(subtotal, discount.value)
      : subtractMoney(discount.value, '0');
  return compareMoney(amount, subtotal) > 0 ? subtractMoney(subtotal, '0') : amount;
}

/** Total indicatif après remise. */
export function discountedTotal(subtotal: string, discount: SaleDiscount | null): string {
  return subtractMoney(subtotal, discountAmount(subtotal, discount));
}

/** Remise enregistrée d'une vente : rechargée comme un montant (le pourcentage n'est qu'une aide). */
export function savedDiscount(sale: {
  discount_amount?: string;
  discount_reason?: string | null;
}): SaleDiscount | null {
  const amount = sale.discount_amount ?? '0';
  return /[1-9]/.test(amount) && sale.discount_reason
    ? { mode: 'AMOUNT', value: amount, reason: sale.discount_reason }
    : null;
}
