export interface UserInfo {
  id: string;
  email: string;
  full_name: string;
  locale: string;
  must_change_password: boolean;
}

export interface MembershipSummary {
  tenant_id: string;
  tenant_name: string;
  tenant_slug: string;
  is_owner: boolean;
}

export interface SessionResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  tenant_id: string | null;
  user: UserInfo;
  memberships: MembershipSummary[];
}

export type SiteKind = 'store' | 'warehouse' | 'restaurant' | 'other';

export interface SiteInfo {
  id: string;
  name: string;
  code: string;
  kind: SiteKind;
}

export type SubscriptionStatus =
  'trial' | 'active' | 'past_due' | 'expired' | 'suspended' | 'cancelled';

/** Secteur d'activité (classification : commerce, restauration…). */
export interface SectorInfo {
  code: string;
  name: string;
  icon: string | null;
}

/** Rubrique du menu déclarée par le profil UX (modules effectifs et implémentés seulement). */
export interface UxNavGroup {
  group: string;
  modules: string[];
}

/** Accents disponibles (palette contrôlée, jetons `--sm-accent*`). */
export type UxAccent = 'blue' | 'green' | 'orange' | 'teal' | 'indigo';

/**
 * Expérience effective du tenant (profil UX + surcharges du profil, restreinte aux modules
 * effectifs). Présentation seulement : les droits restent ceux des permissions.
 */
export interface UxContext {
  code: string | null;
  navigation: UxNavGroup[];
  dashboard: { widgets: string[]; shortcuts: string[] };
  theme: {
    accent: UxAccent | null;
    density: 'comfortable' | 'compact' | null;
    icon: string | null;
  };
  /** Modules planifiés du profil : « à venir », jamais accessibles. */
  upcoming: string[];
}

export interface Capabilities {
  user: UserInfo;
  tenant: {
    id: string;
    name: string;
    slug: string;
    currency: string;
    locale: string;
    timezone: string;
  };
  is_owner: boolean;
  profile: { code: string; name: string; sector: SectorInfo | null; ux_profile: string | null };
  plan: { code: string; name: string };
  subscription: {
    status: SubscriptionStatus;
    billing_period: 'monthly' | 'annual';
    current_period_end: string;
    allowed_access: string[];
  };
  site: SiteInfo | null;
  sites: SiteInfo[];
  modules: { code: string; status: 'available' | 'planned'; core: boolean }[];
  permissions: string[];
  restricted_permissions: string[];
  navigation: string[];
  terminology: Record<string, Record<string, unknown>>;
  ux: UxContext;
  /** Fonctionnalités optionnelles du plan (modules effectifs). */
  features: string[];
  /** Limites du plan : `limit` nul = illimité. */
  limits: Record<string, LimitUsage>;
}

export interface LimitUsage {
  limit: number | null;
  used: number;
}
