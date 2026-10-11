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

/** Élément de la barre supérieure (ex. indicateur des notifications) fourni par un module. */
export interface TopbarItem {
  key: string;
  /** Reçoit le registre des modules (ex. sources du centre de notifications). */
  component: ComponentType<{ modules: readonly FrontendModule[] }>;
  permission?: string;
  feature?: string;
}

/**
 * Source du centre de notifications UNIQUE (indicateur et page `/notifications`) : chaque
 * module déclare la sienne (rappels d'échéance, notifications des membres…) ; une seule
 * cloche, un seul centre, quelles que soient les sources.
 */
export interface NotificationSource {
  key: string;
  /** Clé i18n du titre de la section. */
  labelKey: string;
  /** Droit de consulter la source (ergonomie ; le backend applique le sien). */
  permission: string;
  /** Compteur des non lues (GET, réponse `{ unread }`). */
  unreadPath: string;
  /** Préfixe des clés de requête de la source (invalidé après « marquer comme lu »). */
  queryKey: readonly string[];
  /** Actualisation périodique du compteur (ms). */
  refetchInterval: number;
  /** Liste de la source affichée dans le centre. */
  panel: LazyExoticComponent<ComponentType> | ComponentType;
}

/** Module frontend : même `code` que le module backend correspondant. */
export interface FrontendModule {
  code: string;
  navigation: NavItem[];
  routes: ModuleRoute[];
  topbar?: TopbarItem[];
  notifications?: NotificationSource[];
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
