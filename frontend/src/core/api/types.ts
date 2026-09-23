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
  profile: { code: string; name: string };
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
  /** Fonctionnalités optionnelles du plan (modules effectifs). */
  features: string[];
  /** Limites du plan : `limit` nul = illimité. */
  limits: Record<string, LimitUsage>;
}

export interface LimitUsage {
  limit: number | null;
  used: number;
}
