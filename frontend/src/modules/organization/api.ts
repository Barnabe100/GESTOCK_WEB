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
