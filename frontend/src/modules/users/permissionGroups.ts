import type { Permission } from './api';

export interface ResourceGroup {
  resource: string;
  permissions: Permission[];
}

export interface ModuleGroup {
  module: string;
  resources: ResourceGroup[];
}

/**
 * Regroupe les permissions fournies par l'API par module puis par ressource, dans l'ordre reçu.
 * `matches` filtre (recherche) ; aucune liste de permissions n'est connue du client.
 */
export function groupPermissions(
  permissions: Permission[],
  matches: (permission: Permission) => boolean = () => true,
): ModuleGroup[] {
  const modules = new Map<string, Map<string, Permission[]>>();
  for (const permission of permissions) {
    if (!matches(permission)) continue;
    const resources = modules.get(permission.module) ?? new Map<string, Permission[]>();
    resources.set(permission.resource, [...(resources.get(permission.resource) ?? []), permission]);
    modules.set(permission.module, resources);
  }
  return [...modules.entries()].map(([module, resources]) => ({
    module,
    resources: [...resources.entries()].map(([resource, items]) => ({
      resource,
      permissions: items,
    })),
  }));
}

/** Recherche insensible à la casse et aux accents. */
export function normalizeSearch(value: string): string {
  return value
    .normalize('NFD')
    .replace(/\p{Diacritic}/gu, '')
    .toLowerCase()
    .trim();
}
