/** Types de l'API de la console TechNova (montants en chaînes décimales). */
import type { Page } from '@/shared/lib/serverTable';

export type { Page };

export interface PlatformAdmin {
  id: string;
  email: string;
  full_name: string;
}

export interface PlanCommercial {
  listed: boolean;
  price_display_enabled: boolean;
  monthly_price: string | null;
  monthly_price_enabled: boolean;
  annual_price: string | null;
  annual_price_enabled: boolean;
  currency: string | null;
  contact_required: boolean;
  commercial_description: string | null;
  display_order: number;
  trial_days: number;
}

export interface Plan extends PlanCommercial {
  code: string;
  name: string;
  description: string | null;
  is_active: boolean;
  sort_order: number;
  self_service: boolean;
  updated_at: string;
}

export type AccessKind = 'read' | 'write' | 'export' | 'admin' | 'billing';

export interface PlanStructure {
  modules: { code: string; status: 'available' | 'planned' | null; core: boolean }[];
  features: string[];
  limits: Record<string, number | null>;
  grace_days: number;
  permissions: { code: string; module: string; access: AccessKind; feature: string | null }[];
}

export interface PlanDetail extends Plan {
  structure: PlanStructure;
}

export type PlanCommercialUpdate = Partial<PlanCommercial> & { reason: string };

export interface Dashboard {
  admin: PlatformAdmin;
  plans_total: number;
  plans_active: number;
  plans_listed: number;
  plans_self_service: number;
  plans_contact_required: number;
  modules_available: number;
  modules_planned: number;
  permissions: number;
  profiles_active: number;
  active_countries: number;
  tenants: DashboardTenants;
}

export interface Catalog {
  modules: {
    code: string;
    status: 'available' | 'planned';
    core: boolean;
    depends_on: string[];
    features: string[];
    limits: string[];
    permissions: { code: string; access: AccessKind; feature: string | null }[];
  }[];
  sectors: { code: string; name: string; is_active: boolean }[];
  profiles: {
    code: string;
    name: string;
    sector_code: string | null;
    ux_profile_code: string | null;
    is_active: boolean;
    modules: string[];
  }[];
  ux_profiles: { code: string; name: string; is_active: boolean }[];
  role_templates: {
    code: string;
    name: string;
    description: string | null;
    protected: boolean;
    permission_patterns: string[];
    exclude_patterns: string[];
  }[];
  policies: { status: string; allowed_access: string[]; description: string | null }[];
  currencies: string[];
  active_countries: number;
}

export interface PlatformAuditEntry {
  id: string;
  occurred_at: string;
  actor_user_id: string | null;
  actor_label: string;
  action: string;
  target_type: string | null;
  target_id: string | null;
  tenant_id: string | null;
  before: Record<string, unknown>;
  after: Record<string, unknown>;
  reason: string | null;
  data: Record<string, unknown>;
  ip_address: string | null;
}

// --- Tenants et abonnements (Phase 3.2-G) ------------------------------------------------------

export type TenantStatus = 'active' | 'suspended';
export type SubscriptionStatus =
  'pending_activation' | 'trial' | 'active' | 'past_due' | 'expired' | 'suspended' | 'cancelled';

/** Métadonnées plateforme d'une entreprise (aucune donnée métier). */
export interface TenantListItem {
  id: string;
  name: string;
  trade_name: string | null;
  slug: string;
  status: TenantStatus;
  business_profile_code: string;
  business_profile_name: string;
  created_at: string;
  plan_code: string;
  plan_name: string;
  subscription_status: SubscriptionStatus;
  effective_status: SubscriptionStatus;
  current_period_end: string;
  sites: number;
  users: number;
}

export interface TenantDetail extends TenantListItem {
  country_code: string | null;
  country_name: string | null;
  currency: string;
  locale: string;
  timezone: string;
  usage: Record<string, { used: number; limit: number | null }>;
  subscription: {
    id: string;
    plan_code: string;
    plan_name: string;
    billing_period: 'monthly' | 'annual';
    status: SubscriptionStatus;
    effective_status: SubscriptionStatus;
    started_at: string;
    current_period_start: string;
    current_period_end: string;
    cancelled_at: string | null;
    grace_days: number;
    price_at_subscription: string | null;
    currency_at_subscription: string | null;
  };
  actions: {
    can_suspend: boolean;
    can_reactivate: boolean;
    can_activate: boolean;
    can_extend: boolean;
    can_change_plan: boolean;
    activation_start: string;
    activation_end: string;
    extension_end: string;
    available_plans: { code: string; name: string }[];
  };
}

export interface DashboardTenants {
  tenants_total: number;
  tenants_active: number;
  tenants_suspended: number;
  subscriptions_active: number;
  subscriptions_trial: number;
  subscriptions_pending_activation: number;
  subscriptions_past_due: number;
  subscriptions_expired: number;
  subscriptions_renewal_due: number;
}

export type TenantAction =
  | { kind: 'suspend'; reason: string }
  | { kind: 'reactivate'; reason: string }
  | { kind: 'activate'; reason: string; period_start: string; period_end: string }
  | { kind: 'extend'; reason: string; period_end: string }
  | { kind: 'change-plan'; reason: string; plan_code: string };
