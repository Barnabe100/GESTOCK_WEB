/**
 * Montants et quantités : l'API les échange en chaînes décimales. Le client ne fait AUCUN
 * calcul dessus (règle d'architecture 9) : il valide la saisie et formate l'affichage.
 */

/** Saisie utilisateur → chaîne décimale normalisée (virgule acceptée), ou null si invalide. */
export function normalizeDecimal(input: string, maxDecimals: number): string | null {
  const value = input.trim().replace(/\s/g, '').replace(',', '.');
  if (value === '') return null;
  const pattern = new RegExp(`^\\d{1,15}(\\.\\d{1,${maxDecimals}})?$`);
  return pattern.test(value) ? value : null;
}

/**
 * Affichage d'un montant dans la devise du tenant. Montant entier : format usuel de la devise
 * (XOF sans décimales) ; montant avec centimes : 2 décimales, jamais arrondi à l'affichage.
 */
export function formatMoney(
  value: string | null | undefined,
  currency: string,
  locale = 'fr',
): string {
  if (value === null || value === undefined || value === '') return '';
  const hasCents = /\.\d*[1-9]/.test(value);
  return new Intl.NumberFormat(locale, {
    style: 'currency',
    currency,
    ...(hasCents ? { minimumFractionDigits: 2, maximumFractionDigits: 2 } : {}),
  }).format(Number(value));
}

/** Affichage d'une quantité sans zéros superflus (« 10 », « 10,5 »). */
export function formatQuantity(value: string | null | undefined, locale = 'fr'): string {
  if (value === null || value === undefined || value === '') return '';
  return new Intl.NumberFormat(locale, { maximumFractionDigits: 3 }).format(Number(value));
}

/** Coût unitaire / CMUP (4 décimales en base) : jusqu'à 4 décimales, jamais arrondi à 2. */
export function formatCost(
  value: string | null | undefined,
  currency: string,
  locale = 'fr',
): string {
  if (value === null || value === undefined || value === '') return '';
  const hasDecimals = /\.\d*[1-9]/.test(value);
  return new Intl.NumberFormat(locale, {
    style: 'currency',
    currency,
    ...(hasDecimals ? { minimumFractionDigits: 2, maximumFractionDigits: 4 } : {}),
  }).format(Number(value));
}

/**
 * Arithmétique décimale d'AFFICHAGE (totaux en temps réel), sans float : entiers BigInt à
 * échelle fixe. Le serveur recalcule et fait foi (règle d'architecture 9).
 */
function toScaled(value: string, scale: number): bigint {
  const [whole = '0', fraction = ''] = value.trim().split('.');
  const digits = `${whole}${fraction.padEnd(scale, '0').slice(0, scale)}`;
  return BigInt(digits.replace(/^(-?)0+(?=\d)/, '$1'));
}

function fromScaled(value: bigint, scale: number): string {
  const negative = value < 0n;
  const digits = (negative ? -value : value).toString().padStart(scale + 1, '0');
  const result = `${digits.slice(0, -scale)}.${digits.slice(-scale)}`;
  return negative ? `-${result}` : result;
}

/** quantité (3 déc.) × prix (2 déc.), arrondi au centime demi supérieur : « 0.333 × 0.35 = 0.12 ». */
export function multiplyMoney(quantity: string, price: string): string {
  const product = toScaled(quantity, 3) * toScaled(price, 2); // échelle 5
  const cents = (product + 500n) / 1000n; // montants positifs : demi supérieur
  return fromScaled(cents, 2);
}

/** Somme de montants à 2 décimales. */
export function sumMoney(values: string[]): string {
  return fromScaled(
    values.reduce((total, v) => total + toScaled(v, 2), 0n),
    2,
  );
}
