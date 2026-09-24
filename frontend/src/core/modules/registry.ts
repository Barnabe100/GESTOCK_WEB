import type { FrontendModule, ModuleRoute, NavItem, UiCapabilities } from './types';

/**
 * Construction de l'interface à partir des capacités renvoyées par le backend.
 * Aucune règle ne dépend du secteur d'activité ni du nom du plan : seuls comptent les modules
 * effectifs, les permissions, les fonctionnalités du plan et l'ordre de navigation du profil.
 */

function isAllowed(
  { permission, feature }: { permission?: string; feature?: string },
  caps: UiCapabilities,
): boolean {
  return (
    (permission === undefined || caps.permissions.includes(permission)) &&
    (feature === undefined || (caps.features ?? []).includes(feature))
  );
}

/** Modules frontend dont le module backend est effectif (et implémenté) pour le tenant. */
export function activeModules(
  registry: readonly FrontendModule[],
  caps: UiCapabilities,
): FrontendModule[] {
  const available = new Set(
    caps.modules.filter((m) => m.status === 'available').map((m) => m.code),
  );
  return registry.filter((module) => available.has(module.code));
}

/** Entrées de menu autorisées, dans l'ordre défini par le profil (`caps.navigation`). */
export function buildNavigation(
  registry: readonly FrontendModule[],
  caps: UiCapabilities,
): NavItem[] {
  const order = new Map(caps.navigation.map((code, index) => [code, index]));
  return activeModules(registry, caps)
    .map((module, registryIndex) => ({ module, registryIndex }))
    .sort(
      (a, b) =>
        (order.get(a.module.code) ?? Number.MAX_SAFE_INTEGER) -
          (order.get(b.module.code) ?? Number.MAX_SAFE_INTEGER) ||
        a.registryIndex - b.registryIndex,
    )
    .flatMap(({ module }) => module.navigation.filter((item) => isAllowed(item, caps)));
}

/** Routes autorisées. Une route absente n'est ni déclarée ni chargée. */
export function buildRoutes(
  registry: readonly FrontendModule[],
  caps: UiCapabilities,
): ModuleRoute[] {
  return activeModules(registry, caps).flatMap((module) =>
    module.routes.filter((route) => isAllowed(route, caps)),
  );
}
