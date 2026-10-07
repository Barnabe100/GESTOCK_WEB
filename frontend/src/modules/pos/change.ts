import { normalizeDecimal, subtractMoney, sumMoney } from '@/shared/lib/decimal';

/**
 * Résumé INDICATIF d'un encaissement au point de vente (le serveur recalcule tout à la
 * validation : montant imputé, monnaie rendue, reste dû). Une seule des deux valeurs peut être
 * positive : un reste dû (montant reçu insuffisant) OU une monnaie rendue (montant reçu
 * supérieur au total), jamais les deux.
 *
 * - paiement exact : reste dû 0, monnaie rendue 0 ;
 * - paiement supérieur : monnaie rendue = montant reçu − total ;
 * - paiement inférieur : reste dû = total − montant reçu.
 */
export interface CashSummary {
  total: string;
  /** Montant remis par le client : espèces reçues + montants des autres moyens. */
  received: string;
  remaining: string;
  change: string;
}

export function cashSummary(total: string, amounts: string[]): CashSummary {
  const received = sumMoney(amounts.map((a) => normalizeDecimal(a, 2) ?? '0'));
  const difference = subtractMoney(received, total);
  const overpaid = !difference.startsWith('-');
  return {
    total,
    received,
    remaining: overpaid ? '0.00' : subtractMoney(total, received),
    change: overpaid ? difference : '0.00',
  };
}
