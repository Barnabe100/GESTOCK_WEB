import type {
  FrontendModule,
  ModuleRoute,
  NavGroup,
  NavItem,
  NavSection,
  UiCapabilities,
} from './types';

/**
 * Construction de l'interface à partir des capacités renvoyées par le backend.
 * Aucune règle ne dépend du secteur, du profil d'activité ni du nom du plan : seuls comptent
 * les modules effectifs, les permissions, les fonctionnalités du plan et la présentation
 * déclarée par le profil UX (rubriques, ordre) — des données, jamais des conditions.
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

/** Modules actifs dans l'ordre défini par le profil (`caps.navigation`), puis du registre. */
function orderedModules(
  registry: readonly FrontendModule[],
  caps: UiCapabilities,
): FrontendModule[] {
  const order = new Map(caps.navigation.map((code, index) => [code, index]));
  return activeModules(registry, caps)
    .map((module, registryIndex) => ({ module, registryIndex }))
    .sort(
      (a, b) =>
        (order.get(a.module.code) ?? Number.MAX_SAFE_INTEGER) -
          (order.get(b.module.code) ?? Number.MAX_SAFE_INTEGER) ||
        a.registryIndex - b.registryIndex,
    )
    .map(({ module }) => module);
}

/** Entrées de menu autorisées, dans l'ordre défini par le profil (`caps.navigation`). */
export function buildNavigation(
  registry: readonly FrontendModule[],
  caps: UiCapabilities,
): NavItem[] {
  return orderedModules(registry, caps).flatMap((module) =>
    module.navigation.filter((item) => isAllowed(item, caps)),
  );
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

/**
 * Ordre de repli des rubriques (sans profil UX) : présentation seulement, jamais une règle
 * métier. Une rubrique absente de cette liste est placée à la suite.
 */
export const NAV_GROUPS: readonly NavGroup[] = [
  'home',
  'catalog',
  'stock',
  'sales',
  'cash',
  'reports',
  'admin',
];

/** Regroupe les entrées autorisées par leur rubrique par défaut ; rubriques vides omises. */
export function groupNavigation(items: NavItem[]): NavSection[] {
  const groups = [
    ...NAV_GROUPS,
    ...new Set(items.map((item) => item.group ?? 'home').filter((g) => !NAV_GROUPS.includes(g))),
  ];
  return groups
    .map((group) => ({ group, items: items.filter((item) => (item.group ?? 'home') === group) }))
    .filter((section) => section.items.length > 0);
}

/**
 * Menu final : rubriques et ordre du profil UX (`caps.ux.navigation`), entrées filtrées par
 * module actif, permission et fonctionnalité. Une entrée autorisée que le profil ne place pas
 * n'est jamais perdue : elle rejoint sa rubrique par défaut (créée à la suite au besoin).
 * Sans profil UX : regroupement par rubrique par défaut.
 */
export function buildNavigationSections(
  registry: readonly FrontendModule[],
  caps: UiCapabilities,
): NavSection[] {
  const layout = caps.ux?.navigation ?? [];
  if (layout.length === 0) return groupNavigation(buildNavigation(registry, caps));

  const byModule = new Map(
    orderedModules(registry, caps).map((module) => [
      module.code,
      module.navigation.filter((item) => isAllowed(item, caps)),
    ]),
  );
  const placed = new Set<string>();
  const sections: NavSection[] = layout.map(({ group, modules }) => ({
    group,
    items: modules.flatMap((code) => {
      if (placed.has(code)) return [];
      placed.add(code);
      return byModule.get(code) ?? [];
    }),
  }));
  for (const [code, items] of byModule) {
    if (placed.has(code)) continue;
    for (const item of items) {
      const group = item.group ?? 'home';
      let section = sections.find((s) => s.group === group);
      if (!section) {
        section = { group, items: [] };
        sections.push(section);
      }
      section.items.push(item);
    }
  }
  return sections.filter((section) => section.items.length > 0);
}
