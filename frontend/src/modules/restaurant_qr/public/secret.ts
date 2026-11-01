/**
 * Secret d'une page publique (jeton du point ou secret de suivi), porté par le FRAGMENT de
 * l'adresse (`#…`) — jamais envoyé au serveur par le navigateur. À l'ouverture : lu, gardé pour
 * l'onglet (`sessionStorage`, pour survivre à un rechargement) puis EFFACÉ de la barre d'adresse
 * (`history.replaceState`) : il n'apparaît ni dans l'historique, ni dans un partage d'écran, ni
 * dans un référent.
 */

const SECRET_RE = /^[A-Za-z0-9_-]{43}$/;

function read(storageKey: string): string | null {
  try {
    return sessionStorage.getItem(storageKey);
  } catch {
    return null;
  }
}

function keep(storageKey: string, value: string): void {
  try {
    sessionStorage.setItem(storageKey, value);
  } catch {
    // stockage indisponible (navigation privée stricte) : le secret reste en mémoire
  }
}

/** Lit le secret du fragment (prioritaire) ou de l'onglet, puis efface le fragment. */
export function takeFragmentSecret(storageKey: string): string | null {
  const fromHash = window.location.hash.replace(/^#/, '');
  const stored = SECRET_RE.test(fromHash) ? null : read(storageKey);
  const secret = SECRET_RE.test(fromHash)
    ? fromHash
    : stored !== null && SECRET_RE.test(stored)
      ? stored
      : null;
  if (secret === fromHash) keep(storageKey, fromHash);
  if (window.location.hash !== '') {
    window.history.replaceState(window.history.state, '', window.location.pathname);
  }
  return secret;
}

/** Politique de sécurité du contenu des pages publiques : aucune ressource tierce. */
export const PUBLIC_CSP = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data:",
  "font-src 'self' data:",
  "connect-src 'self'",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'self'",
].join('; ');

/** Ajoute (une fois) la politique de sécurité et la politique de référent au document. */
export function applyPublicPagePolicies(): void {
  if (document.querySelector('meta[data-public-csp]') === null) {
    const csp = document.createElement('meta');
    csp.httpEquiv = 'Content-Security-Policy';
    csp.content = PUBLIC_CSP;
    csp.dataset.publicCsp = 'true';
    document.head.prepend(csp);
  }
  if (document.querySelector('meta[name="referrer"]') === null) {
    const referrer = document.createElement('meta');
    referrer.name = 'referrer';
    referrer.content = 'no-referrer';
    document.head.prepend(referrer);
  }
}
