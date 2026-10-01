import { formatQuantity } from './decimal';

/**
 * Présentations et équivalences d'AFFICHAGE (Lot 3-C, ADR-0041) : le stock est en unité de
 * base, chaque conditionnement en est un multiple (Carton 24 = 24 bouteilles). Calcul décimal
 * exact (BigInt, 3 décimales), aucun float ; le serveur recalcule et fait foi.
 */
export interface PresentationPackaging {
  id: string;
  name: string;
  conversion: string;
}

const SCALE = 1000n;

function scaled(value: string): bigint {
  const [whole = '0', fraction = ''] = value.trim().split('.');
  const negative = whole.startsWith('-');
  const digits = BigInt(`${whole.replace('-', '')}${fraction.padEnd(3, '0').slice(0, 3)}`);
  return negative ? -digits : digits;
}

function unscaled(value: bigint): string {
  const negative = value < 0n;
  const digits = (negative ? -value : value).toString().padStart(4, '0');
  const text = `${digits.slice(0, -3)}.${digits.slice(-3)}`;
  return negative ? `-${text}` : text;
}

/** Quantité de base = quantité × conversion (exacte) ; `null` au-delà de 3 décimales. */
export function toBase(quantity: string, conversion: string): string | null {
  const product = scaled(quantity) * scaled(conversion); // échelle 6
  return product % SCALE === 0n ? unscaled(product / SCALE) : null;
}

export interface Equivalence {
  name: string;
  /** Nombre de conditionnements (entier, ou décimal exact pour un article décimal). */
  quantity: string;
  /** Unités de base restantes (« 2 Carton 24 + 2 bouteilles ») ; nul si division exacte. */
  remainder: string | null;
}

/**
 * Équivalences PERTINENTES d'une quantité de base dans les conditionnements configurés
 * (48 bouteilles = 8 Pack 6 = 2 Carton 24) : seulement ceux dont elle contient au moins un
 * conditionnement, du plus petit au plus grand, au plus `max` pour ne pas surcharger l'écran.
 * Division exacte : quotient ; article décimal : quotient décimal exact (1,5 Sac) ; sinon
 * quotient entier + reste en unité de base.
 */
export function equivalences(
  base: string,
  packagings: PresentationPackaging[],
  decimalAllowed = false,
  max = 3,
): Equivalence[] {
  const total = scaled(base);
  if (total <= 0n) return [];
  return [...packagings]
    .sort((a, b) => (scaled(a.conversion) < scaled(b.conversion) ? -1 : 1))
    .filter((p) => scaled(p.conversion) > 0n && total >= scaled(p.conversion))
    .slice(0, max)
    .map((p) => {
      const conversion = scaled(p.conversion);
      const exact = (total * SCALE) % conversion === 0n;
      if (total % conversion === 0n) {
        return { name: p.name, quantity: (total / conversion).toString(), remainder: null };
      }
      if (decimalAllowed && exact) {
        return { name: p.name, quantity: unscaled((total * SCALE) / conversion), remainder: null };
      }
      const whole = total / conversion;
      return {
        name: p.name,
        quantity: whole.toString(),
        remainder: unscaled(total - whole * conversion),
      };
    });
}

/** « = 8 Pack 6 » / « = 2 Carton 24 + 2 bouteilles » (texte affiché, quantités localisées). */
export function formatEquivalence(e: Equivalence, unit: string, locale = 'fr'): string {
  const main = `= ${formatQuantity(e.quantity, locale)} ${e.name}`;
  return e.remainder === null ? main : `${main} + ${formatQuantity(e.remainder, locale)} ${unit}`;
}

/** « 2 Carton 24 = 48 bouteilles » (présentation saisie → unité de base). */
export function formatPresented(
  quantity: string,
  packagingName: string | null | undefined,
  base: string | null | undefined,
  unit: string,
  locale = 'fr',
): string {
  if (!packagingName) return `${formatQuantity(quantity, locale)} ${unit}`;
  const presented = `${formatQuantity(quantity, locale)} ${packagingName}`;
  return base ? `${presented} = ${formatQuantity(base, locale)} ${unit}` : presented;
}
