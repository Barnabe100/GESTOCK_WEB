/** Types de l'API de la console TechNova (montants en chaînes décimales). */
import type { Page } from '@/shared/lib/serverTable';
import type { LicenseState, SubscriptionPaymentStatus } from '@/shared/ui/StatusBadge';

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

/** Métadonnées plateforme d'une entreprise (aucune donnée métier). 1 site = 1 abonnement
 * (ADR-0033) : abonnements résumés (plans, statuts effectifs, prochaine échéance). */
export interface TenantListItem {
  id: string;
  name: string;
  trade_name: string | null;
  slug: string;
  status: TenantStatus;
  business_profile_code: string;
  business_profile_name: string;
  created_at: string;
  subscription_count: number;
  plan_codes: string[];
  effective_statuses: SubscriptionStatus[];
  next_period_end: string | null;
  sites: number;
  users: number;
}

/** Abonnement d'un site (``site`` nul : pris à l'inscription, en attente du premier site). */
export interface TenantSubscription {
  id: string;
  site: { id: string; name: string; code: string } | null;
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
  requested_activations: number;
  usage: Record<string, { used: number; limit: number | null }>;
  /** Licence en vigueur du site, sinon la plus récente (ADR-0034). */
  license: LicenseSummary | null;
  actions: {
    can_activate: boolean;
    can_extend: boolean;
    can_change_plan: boolean;
    activation_start: string;
    activation_end: string;
    extension_end: string;
    available_plans: { code: string; name: string }[];
  };
}

export interface TenantDetail extends TenantListItem {
  country_code: string | null;
  country_name: string | null;
  currency: string;
  locale: string;
  timezone: string;
  subscriptions: TenantSubscription[];
  actions: { can_suspend: boolean; can_reactivate: boolean };
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
  | {
      kind: 'activate';
      subscription_id: string;
      reason: string;
      period_start: string;
      period_end: string;
    }
  | { kind: 'extend'; subscription_id: string; reason: string; period_end: string }
  | { kind: 'change-plan'; subscription_id: string; reason: string; plan_code: string };

// --- Paiements d'abonnement (Phase 3.3-A) ------------------------------------------------------

export type { SubscriptionPaymentStatus };

/** Paiement vu par TechNova : ni identité du déclarant, ni donnée métier de l'entreprise. */
export interface ConsolePayment {
  id: string;
  tenant_id: string;
  tenant_name: string;
  subscription_id: string;
  plan_code: string;
  site_id: string | null;
  site_name: string | null;
  amount: string;
  currency: string;
  period_start: string;
  period_end: string;
  payment_method: string;
  declared_reference: string;
  status: SubscriptionPaymentStatus;
  created_at: string;
  decided_at: string | null;
  decided_by_email: string | null;
  rejection_reason: string | null;
}

export type PaymentDecision = { kind: 'confirm' | 'reject'; reason: string };

// --- Licences (Phase 3.3-B2, ADR-0034) ---------------------------------------------------------

export type { LicenseState };

export interface LicenseSummary {
  id: string;
  license_number: string;
  license_version: number;
  state: LicenseState;
  plan_code: string;
  valid_from: string;
  valid_until: string;
  max_activations: number;
  activations_used: number;
  activations_available: number;
  issued_at: string;
  revoked_at: string | null;
}

/** Licence vue par TechNova : contenu signé (jamais modifiable), état calculé, révocation. */
export interface ConsoleLicense {
  id: string;
  license_number: string;
  license_version: number;
  supersedes_id: string | null;
  superseded_by_id: string | null;
  tenant_id: string;
  tenant_name: string;
  site_id: string;
  site_name: string;
  site_code: string;
  subscription_id: string;
  payment_id: string;
  plan_code: string;
  billing_period: 'monthly' | 'annual';
  valid_from: string;
  valid_until: string;
  timezone: string;
  max_activations: number;
  /** Postes actifs sur l'abonnement du site (3.3-B3). */
  activations_used: number;
  modules: string[];
  features: string[];
  limits: Record<string, number | null>;
  status: 'ISSUED' | 'REVOKED';
  state: LicenseState;
  issued_at: string;
  issued_by_email: string | null;
  key_id: string;
  payload_sha256: string;
  revoked_at: string | null;
  revoked_by_email: string | null;
  revocation_reason: string | null;
}

/** Ce que produirait la génération depuis un paiement (calculé par le serveur). */
export interface LicenseProposal {
  payment_id: string;
  payment_status: SubscriptionPaymentStatus;
  tenant_id: string;
  tenant_name: string;
  subscription_id: string;
  site_id: string | null;
  site_name: string | null;
  plan_code: string;
  billing_period: 'monthly' | 'annual';
  timezone: string;
  valid_from: string;
  valid_until: string;
  requested_activations: number;
  max_activations: number;
  payment_period_start: string;
  payment_period_end: string;
  blocking: string | null;
  license_id: string | null;
}

export type LicenseAction =
  | { kind: 'revoke'; reason: string }
  | { kind: 'reissue'; reason: string; max_activations?: number };

/** Poste d'un site (installation cliente) : jamais l'utilisateur de l'entreprise qui l'a activé. */
export interface ConsoleActivation {
  id: string;
  tenant_id: string;
  tenant_name: string;
  site_id: string;
  site_name: string;
  subscription_id: string;
  license_id: string;
  license_number: string;
  installation_id: string;
  label: string;
  client_version: string | null;
  status: 'ACTIVE' | 'RELEASED';
  activated_at: string;
  last_seen_at: string;
  released_at: string | null;
  release_source: 'TENANT' | 'TECHNOVA' | null;
  release_reason: string | null;
}
