/**
 * Préférences de navigation propres à l'onglet (sessionStorage) : entreprise et site choisis.
 * Ce ne sont PAS des éléments de sécurité : le backend revalide tout à chaque requête.
 */
const TENANT_KEY = 'sm.tenantId';
const SITE_KEY_PREFIX = 'sm.siteId.';

function safe<T>(fn: () => T, fallback: T): T {
  try {
    return fn();
  } catch {
    return fallback;
  }
}

export const tabStorage = {
  getTenantId: () => safe(() => sessionStorage.getItem(TENANT_KEY), null),
  setTenantId: (tenantId: string | null) =>
    safe(() => {
      if (tenantId) sessionStorage.setItem(TENANT_KEY, tenantId);
      else sessionStorage.removeItem(TENANT_KEY);
    }, undefined),
  getSiteId: (tenantId: string) =>
    safe(() => sessionStorage.getItem(SITE_KEY_PREFIX + tenantId), null),
  setSiteId: (tenantId: string, siteId: string | null) =>
    safe(() => {
      if (siteId) sessionStorage.setItem(SITE_KEY_PREFIX + tenantId, siteId);
      else sessionStorage.removeItem(SITE_KEY_PREFIX + tenantId);
    }, undefined),
};
