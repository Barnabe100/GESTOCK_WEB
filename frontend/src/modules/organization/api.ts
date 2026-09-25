import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/core/api/client';
import type { SiteKind } from '@/core/api/types';

export interface Tenant {
  id: string;
  name: string;
  slug: string;
  business_profile_code: string;
  currency: string;
  locale: string;
  timezone: string;
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
}

export interface SiteInput {
  name: string;
  code: string;
  kind: SiteKind;
  address?: string | null;
  phone?: string | null;
  is_active?: boolean;
}

export interface TenantModule {
  code: string;
  status: 'available' | 'planned';
  core: boolean;
  depends_on: string[];
  in_profile: boolean;
  in_plan: boolean;
  enabled: boolean;
  effective: boolean;
}

export const orgKeys = {
  tenant: ['organization', 'tenant'] as const,
  sites: ['organization', 'sites'] as const,
  modules: ['organization', 'modules'] as const,
};

export function useTenant() {
  return useQuery({
    queryKey: orgKeys.tenant,
    queryFn: ({ signal }) => api.get<Tenant>('/tenant', signal),
  });
}

export function useUpdateTenant() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { name?: string; timezone?: string }) =>
      api.patch<Tenant>('/tenant', input),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: orgKeys.tenant });
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

export function useModules() {
  return useQuery({
    queryKey: orgKeys.modules,
    queryFn: ({ signal }) => api.get<TenantModule[]>('/modules', signal),
  });
}

export function useToggleModule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ code, enabled }: { code: string; enabled: boolean }) =>
      api.put<void>(`/modules/${code}`, { enabled }),
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
export function useChangeBusinessProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (code: string) => api.put<Tenant>('/tenant/business-profile', { code }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: orgKeys.tenant });
      void qc.invalidateQueries({ queryKey: orgKeys.modules });
      // Menu, tableau de bord, terminologie et thème sont relus depuis les capacités.
      void qc.invalidateQueries({ queryKey: ['capabilities'] });
    },
  });
}
