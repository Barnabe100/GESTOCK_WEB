import type { ComponentType, LazyExoticComponent } from 'react';

/**
 * Rubrique de la barre latérale : identifiant libre (libellé i18n `navGroups.<id>`). L'ordre et
 * le contenu viennent du profil UX (`caps.ux.navigation`) ; `NAV_GROUPS` sert de repli.
 */
export type NavGroup = string;

export interface NavItem {
  /** Identifiant unique de l'entrée. */
  key: string;
  /** Clé i18n du libellé (espace de noms `common`). */
  labelKey: string;
  /** Classe d'icône PrimeIcons (ex. `pi pi-home`). */
  icon: string;
  path: string;
  /** Permission requise pour afficher l'entrée (ergonomie ; le backend applique la sienne). */
  permission?: string;
  /** Fonctionnalité de plan requise en plus (ex. `stock.transfers`). */
  feature?: string;
  /** Rubrique par défaut, si le profil UX ne place pas le module (défaut : `home`). */
  group?: NavGroup;
}

export interface ModuleRoute {
  path: string;
  component: LazyExoticComponent<ComponentType> | ComponentType;
  permission?: string;
  feature?: string;
}

/** Module frontend : même `code` que le module backend correspondant. */
export interface FrontendModule {
  code: string;
  navigation: NavItem[];
  routes: ModuleRoute[];
}

/** Sous-ensemble des capacités utile à la construction de l'interface. */
export interface UiCapabilities {
  modules: { code: string; status: string }[];
  permissions: string[];
  navigation: string[];
  /** Fonctionnalités optionnelles du plan (absentes = aucune). */
  features?: string[];
  /** Rubriques du profil UX (absentes : regroupement par rubrique par défaut des entrées). */
  ux?: { navigation: { group: string; modules: string[] }[] };
}

/** Rubrique construite : entrées autorisées, dans l'ordre du profil UX. */
export interface NavSection {
  group: NavGroup;
  items: NavItem[];
}
