import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/core/api/client';
import type { SiteKind } from '@/core/api/types';

/** Informations d'entreprise modifiables : recommandées (reçus, documents) et facultatives. */
export const RECOMMENDED_COMPANY_FIELDS = [
  'trade_name',
  'logo_url',
  'email',
  'phone',
  'address',
  'city',
  'region',
  'tax_id',
  'trade_register',
] as const;
export const OPTIONAL_COMPANY_FIELDS = ['website', 'description'] as const;
export type CompanyField =
  (typeof RECOMMENDED_COMPANY_FIELDS)[number] | (typeof OPTIONAL_COMPANY_FIELDS)[number];

/** Entreprise : source unique de son identité (documents, reçus). */
export interface Tenant extends Record<CompanyField, string | null> {
  id: string;
  name: string;
  slug: string;
  business_profile_code: string;
  /** Figée à la création. */
  currency: string;
  locale: string;
  timezone: string;
  /** Nul : entreprise antérieure à la 3.2, pays à renseigner. */
  country_code: string | null;
}

/** Champ omis : inchangé ; `null` : effacé (champs recommandés et facultatifs seulement). */
export type TenantInput = Partial<Record<CompanyField, string | null>> & {
  name?: string;
  timezone?: string;
  country_code?: string;
};

export interface IdentityLine {
  kind: 'phone' | 'email' | 'address' | 'locality' | 'tax_id' | 'trade_register';
  value: string;
}

/** En-tête documentaire construit par le serveur à partir du tenant (lignes absentes omises). */
export interface DocumentIdentity {
  name: string;
  trade_name: string | null;
  logo_url: string | null;
  contact: IdentityLine[];
  identifiers: IdentityLine[];
  missing_recommended: string[];
}

export interface Site {
  id: string;
  name: string;
  code: string;
  kind: SiteKind;
  address: string | null;
  phone: string | null;
  is_active: boolean;
  created_at: string;
  /** Profil d'activité du site (palier D : changé par `PUT /sites/{id}/business-profile`). */
  business_profile_code: string;
}

export interface SiteInput {
  name: string;
  code: string;
  kind: SiteKind;
  address?: string | null;
  phone?: string | null;
  is_active?: boolean;
  /** Création : abonnement du site (offre publiée, période, postes demandés), sauf pour le
   * premier site d'une inscription (abonnement déjà choisi). */
  plan_code?: string;
  billing_period?: 'monthly' | 'annual';
  requested_activations?: number;
  /** Création : profil du site (absent → profil d'origine de l'entreprise). */
  business_profile_code?: string;
}

/** État d'un module sur UN site (palier C) : effectif = profil ∩ plan ∩ activation du site. */
export interface SiteModule {
  code: string;
  status: 'available' | 'planned';
  core: boolean;
  depends_on: string[];
  in_profile: boolean;
  in_plan: boolean;
  activated_for_site: boolean;
  effective: boolean;
}

export const orgKeys = {
  tenant: ['organization', 'tenant'] as const,
  documentIdentity: ['organization', 'tenant', 'document-identity'] as const,
  sites: ['organization', 'sites'] as const,
  modules: ['organization', 'modules'] as const,
};

export function useTenant() {
  return useQuery({
    queryKey: orgKeys.tenant,
    queryFn: ({ signal }) => api.get<Tenant>('/tenant', signal),
  });
}

export function useDocumentIdentity() {
  return useQuery({
    queryKey: orgKeys.documentIdentity,
    queryFn: ({ signal }) => api.get<DocumentIdentity>('/tenant/document-identity', signal),
  });
}

export function useUpdateTenant() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: TenantInput) => api.patch<Tenant>('/tenant', input),
    onSuccess: () => {
      // Préfixe commun : entreprise et identité documentaire.
      void qc.invalidateQueries({ queryKey: orgKeys.tenant });
      void qc.invalidateQueries({ queryKey: ['organization', 'onboarding'] });
      void qc.invalidateQueries({ queryKey: ['capabilities'] });
    },
  });
}

export function useSites() {
  return useQuery({
    queryKey: orgKeys.sites,
    queryFn: ({ signal }) => api.get<Site[]>('/sites', signal),
  });
}

export function useSaveSite() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id?: string; input: SiteInput }) =>
      id ? api.patch<Site>(`/sites/${id}`, input) : api.post<Site>('/sites', input),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: orgKeys.sites });
      void qc.invalidateQueries({ queryKey: ['capabilities'] });
    },
  });
}

export function useSiteModules(siteId: string | null) {
  return useQuery({
    queryKey: [...orgKeys.modules, siteId],
    queryFn: ({ signal }) => api.get<SiteModule[]>(`/sites/${siteId}/modules`, signal),
    enabled: siteId !== null,
  });
}

/** Activation sur UN site : portée explicite dans l'URL (jamais l'en-tête du site actif). */
export function useToggleSiteModule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ siteId, code, enabled }: { siteId: string; code: string; enabled: boolean }) =>
      api.put<void>(`/sites/${siteId}/modules/${code}`, { enabled }),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: orgKeys.modules });
      void qc.invalidateQueries({ queryKey: ['capabilities'] });
    },
  });
}

/** Catalogue des profils d'activité (global, identique pour tous les tenants). */
export interface BusinessProfileSummary {
  code: string;
  name: string;
  description: string | null;
  sector: string | null;
  ux_profile: string | null;
  sort_order: number;
  is_active: boolean;
  default_modules: string[];
  optional_modules: string[];
}

export interface BusinessProfileCatalog {
  sectors: { code: string; name: string; icon: string | null; sort_order: number }[];
  profiles: BusinessProfileSummary[];
}

export function useBusinessProfiles(enabled: boolean) {
  return useQuery({
    queryKey: ['organization', 'business-profiles'],
    queryFn: ({ signal }) => api.get<BusinessProfileCatalog>('/business-profiles', signal),
    // Catalogue stable : aucune relecture pendant la session.
    staleTime: Infinity,
    enabled,
  });
}

/** Changement de profil : le serveur contrôle, audite et ne supprime aucune donnée. */

/** Aperçu du changement de profil d'UN site (palier D) : tout est calculé par le serveur. */
export type ProfileChangeLevel = 'SIMPLE' | 'STRONG' | 'BLOCKED';

export interface ProfileModuleState {
  in_profile: boolean;
  in_plan: boolean;
  activated: boolean;
  effective: boolean;
}

export interface ProfileModuleChange {
  code: string;
  status: 'available' | 'planned';
  change: 'added' | 'removed' | 'kept';
  action: 'enable' | 'disable' | 'none';
  reason: string | null;
  before: ProfileModuleState;
  after: ProfileModuleState;
}

export interface FootprintItem {
  kind: string;
  module: string;
  count: number;
  capped: boolean;
  blocking: boolean;
}

export interface SiteProfilePreview {
  site_id: string;
  site_name: string;
  current_profile: { code: string; name: string; sector: string | null };
  target_profile: { code: string; name: string; sector: string | null };
  level: ProfileChangeLevel;
  fingerprint: string;
  confirmation_text: string | null;
  plan: { code: string; compatibility: 'FULL' | 'PARTIAL'; modules_not_in_plan: string[] };
  modules: ProfileModuleChange[];
  summary: {
    added: string[];
    removed: string[];
    kept: string[];
    not_in_plan: string[];
    activated: string[];
    deactivated: string[];
  };
  history: FootprintItem[];
  open_operations: FootprintItem[];
  blockers: FootprintItem[];
  configuration: FootprintItem[];
  assortment_unchanged: boolean;
}

export function useSiteProfilePreview(siteId: string, profileCode: string | null) {
  return useQuery({
    queryKey: [...orgKeys.sites, siteId, 'profile-preview', profileCode],
    queryFn: ({ signal }) =>
      api.get<SiteProfilePreview>(
        `/sites/${siteId}/business-profile/preview?${new URLSearchParams({
          profile_code: profileCode ?? '',
        }).toString()}`,
        signal,
      ),
    enabled: profileCode !== null,
    // Toujours l'état courant : l'aperçu n'est jamais servi depuis le cache.
    staleTime: 0,
    gcTime: 0,
  });
}

/** Changement réel : le serveur recalcule l'aperçu sous verrou et compare l'empreinte. */
export function useChangeSiteProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      siteId,
      ...body
    }: {
      siteId: string;
      profile_code: string;
      preview_fingerprint: string;
      confirmation?: string;
    }) => api.put<unknown>(`/sites/${siteId}/business-profile`, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: orgKeys.sites });
      void qc.invalidateQueries({ queryKey: orgKeys.modules });
      void qc.invalidateQueries({ queryKey: ['capabilities'] });
    },
  });
}
