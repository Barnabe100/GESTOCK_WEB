import { multiplyMoney, normalizeDecimal, sumMoney } from '@/shared/lib/decimal';

import type { PosArticle } from './api';

/**
 * Panier du point de vente : état d'AFFICHAGE uniquement. Les montants sont indicatifs (calcul
 * décimal exact, sans float) ; le serveur relit les prix, recalcule les totaux et contrôle le
 * stock à l'encaissement.
 */
export interface CartLine {
  article: PosArticle;
  /** Quantité saisie (chaîne décimale, 3 décimales au plus). */
  quantity: string;
}

export type CartAction =
  | { type: 'add'; article: PosArticle }
  | { type: 'set'; articleId: string; quantity: string }
  | { type: 'step'; articleId: string; delta: 1 | -1 }
  | { type: 'remove'; articleId: string }
  | { type: 'clear' };

function stepQuantity(quantity: string, delta: 1 | -1): string {
  const current = Number(normalizeDecimal(quantity, 3) ?? '0');
  // Pas d'une unité ; jamais en dessous de 1 par les boutons (la suppression est explicite).
  return String(Math.max(1, Math.floor(current) + delta));
}

/** Un article n'apparaît qu'une fois : l'ajouter à nouveau augmente sa quantité. */
export function cartReducer(lines: CartLine[], action: CartAction): CartLine[] {
  switch (action.type) {
    case 'add': {
      if (!action.article.is_active) return lines;
      const existing = lines.find((l) => l.article.article_id === action.article.article_id);
      if (existing) {
        return lines.map((l) =>
          l === existing ? { ...l, quantity: stepQuantity(l.quantity, 1) } : l,
        );
      }
      return [...lines, { article: action.article, quantity: '1' }];
    }
    case 'set':
      return lines.map((l) =>
        l.article.article_id === action.articleId ? { ...l, quantity: action.quantity } : l,
      );
    case 'step':
      return lines.map((l) =>
        l.article.article_id === action.articleId
          ? { ...l, quantity: stepQuantity(l.quantity, action.delta) }
          : l,
      );
    case 'remove':
      return lines.filter((l) => l.article.article_id !== action.articleId);
    case 'clear':
      return [];
  }
}

/** Quantité valide (> 0, 3 décimales au plus), normalisée ; sinon null. */
export function validQuantity(quantity: string): string | null {
  const normalized = normalizeDecimal(quantity, 3);
  return normalized !== null && /[1-9]/.test(normalized) ? normalized : null;
}

export function lineTotal(line: CartLine): string | null {
  const quantity = validQuantity(line.quantity);
  return quantity ? multiplyMoney(quantity, line.article.sale_price) : null;
}

export function cartTotal(lines: CartLine[]): string {
  return sumMoney(lines.map(lineTotal).filter((v): v is string => v !== null));
}
