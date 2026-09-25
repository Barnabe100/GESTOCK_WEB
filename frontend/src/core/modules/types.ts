import type { ComponentType, LazyExoticComponent } from 'react';

/** Rubrique de la barre latérale (ordre : NAV_GROUPS). */
export type NavGroup = 'home' | 'catalog' | 'stock' | 'sales' | 'cash' | 'admin';

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
  /** Rubrique d'affichage (défaut : `home`). */
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
}
