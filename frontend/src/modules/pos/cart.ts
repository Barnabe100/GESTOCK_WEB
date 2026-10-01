import {
  compareQuantity,
  isWholeQuantity,
  multiplyMoney,
  multiplyQuantity,
  normalizeDecimal,
  sumMoney,
} from '@/shared/lib/decimal';

import type { PosArticle, PosPackaging } from './api';

/**
 * Panier du point de vente : état d'AFFICHAGE uniquement. Les montants sont indicatifs (calcul
 * décimal exact, sans float) ; le serveur relit les prix, les conditionnements, la règle des
 * quantités entières, recalcule les totaux et contrôle le stock à l'encaissement. Le panier ne
 * réserve aucun stock.
 */
export interface CartLine {
  article: PosArticle;
  /** Lot 3-B : conditionnement choisi ; `null` = unité de base (toujours disponible). */
  packaging: PosPackaging | null;
  /** Quantité saisie dans la présentation choisie (chaîne décimale, 3 décimales au plus). */
  quantity: string;
}

export type CartAction =
  | { type: 'add'; article: PosArticle; packaging?: PosPackaging | null }
  | { type: 'set'; key: string; quantity: string }
  | { type: 'step'; key: string; delta: 1 | -1 }
  | { type: 'packaging'; key: string; packagingId: string | null }
  | { type: 'remove'; key: string }
  | { type: 'clear' };

/** Une ligne par présentation : article en unité de base, ou article × conditionnement. */
export function lineKey(line: Pick<CartLine, 'article' | 'packaging'>): string {
  return `${line.article.article_id}:${line.packaging?.id ?? 'base'}`;
}

function stepQuantity(quantity: string, delta: 1 | -1): string {
  const current = Number(normalizeDecimal(quantity, 3) ?? '0');
  // Pas d'une unité ; jamais en dessous de 1 par les boutons (la suppression est explicite).
  return String(Math.max(1, Math.floor(current) + delta));
}

export function cartReducer(lines: CartLine[], action: CartAction): CartLine[] {
  switch (action.type) {
    case 'add': {
      if (!action.article.is_active) return lines;
      const added = { article: action.article, packaging: action.packaging ?? null };
      const existing = lines.find((l) => lineKey(l) === lineKey(added));
      if (existing) {
        return lines.map((l) =>
          l === existing ? { ...l, quantity: stepQuantity(l.quantity, 1) } : l,
        );
      }
      return [...lines, { ...added, quantity: '1' }];
    }
    case 'set':
      return lines.map((l) =>
        lineKey(l) === action.key ? { ...l, quantity: action.quantity } : l,
      );
    case 'step':
      return lines.map((l) =>
        lineKey(l) === action.key ? { ...l, quantity: stepQuantity(l.quantity, action.delta) } : l,
      );
    case 'packaging': {
      // Changement de présentation : prix, quantité de base et total recalculés ; si la
      // présentation choisie existe déjà dans le panier, les deux lignes fusionnent.
      const line = lines.find((l) => lineKey(l) === action.key);
      if (!line) return lines;
      const packaging =
        action.packagingId === null
          ? null
          : (line.article.packagings.find((p) => p.id === action.packagingId) ?? null);
      const changed = { ...line, packaging };
      const target = lines.find((l) => l !== line && lineKey(l) === lineKey(changed));
      if (!target) return lines.map((l) => (l === line ? changed : l));
      const merged = sumQuantities(target.quantity, line.quantity);
      return lines
        .filter((l) => l !== line)
        .map((l) => (l === target ? { ...l, quantity: merged ?? l.quantity } : l));
    }
    case 'remove':
      return lines.filter((l) => lineKey(l) !== action.key);
    case 'clear':
      return [];
  }
}

function sumQuantities(a: string, b: string): string | null {
  const x = normalizeDecimal(a, 3);
  const y = normalizeDecimal(b, 3);
  return x === null || y === null ? null : addQuantities(x, y);
}

/** Somme exacte de deux quantités (3 décimales, sans float), sans zéros superflus. */
function addQuantities(a: string, b: string): string {
  const scale = (v: string) => {
    const [whole = '0', fraction = ''] = v.split('.');
    return BigInt(`${whole}${fraction.padEnd(3, '0').slice(0, 3)}`);
  };
  const total = (scale(a) + scale(b)).toString().padStart(4, '0');
  return `${total.slice(0, -3)}.${total.slice(-3)}`.replace(/\.?0+$/, '');
}

/** Quantité valide (> 0, 3 décimales au plus), normalisée ; sinon null. */
export function validQuantity(quantity: string): string | null {
  const normalized = normalizeDecimal(quantity, 3);
  return normalized !== null && /[1-9]/.test(normalized) ? normalized : null;
}

/** Prix unitaire de la présentation : conditionnement ou article (unité de base). */
export function unitPrice(line: CartLine): string {
  return line.packaging?.sale_price ?? line.article.sale_price;
}

/**
 * Quantité de la ligne si elle respecte les règles de l'article (guidage ; le serveur
 * revérifie) : > 0, entière pour un article sans quantités décimales, quantité de base de
 * 3 décimales au plus. Sinon `null`.
 */
export function lineQuantity(line: CartLine): string | null {
  const quantity = validQuantity(line.quantity);
  if (quantity === null) return null;
  const base = baseQuantity(line, quantity);
  if (base === null) return null;
  if (
    !line.article.decimal_quantity_allowed &&
    !(isWholeQuantity(quantity) && isWholeQuantity(base))
  )
    return null;
  return quantity;
}

/** Quantité en unité de base (indicative) : quantité × conversion. */
export function baseQuantity(
  line: CartLine,
  quantity = validQuantity(line.quantity),
): string | null {
  if (quantity === null) return null;
  return line.packaging ? multiplyQuantity(quantity, line.packaging.conversion) : quantity;
}

export function lineTotal(line: CartLine): string | null {
  const quantity = lineQuantity(line);
  return quantity ? multiplyMoney(quantity, unitPrice(line)) : null;
}

export function cartTotal(lines: CartLine[]): string {
  return sumMoney(lines.map(lineTotal).filter((v): v is string => v !== null));
}

/**
 * Disponibilité indicative : total des quantités de base de l'article dans le panier (toutes
 * présentations confondues) comparé au stock affiché du site. Article non géré : toujours
 * disponible. Le serveur contrôle le stock à l'encaissement.
 */
export function exceedsStock(lines: CartLine[], line: CartLine): boolean {
  if (!line.article.stock_managed) return false;
  const total = lines
    .filter((l) => l.article.article_id === line.article.article_id)
    .map((l) => baseQuantity(l) ?? '0')
    .reduce(addQuantities, '0');
  return compareQuantity(total, line.article.quantity) > 0;
}
