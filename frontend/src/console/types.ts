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
