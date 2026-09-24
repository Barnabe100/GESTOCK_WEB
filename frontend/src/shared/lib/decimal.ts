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
