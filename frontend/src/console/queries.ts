import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { consoleRequest } from './api';
import type {
  Catalog,
  Dashboard,
  Page,
  Plan,
  PlanCommercialUpdate,
  PlanDetail,
  PlatformAuditEntry,
  TenantAction,
  TenantDetail,
  TenantListItem,
} from './types';

export const useDashboard = () =>
  useQuery({
    queryKey: ['console', 'dashboard'],
    queryFn: () => consoleRequest<Dashboard>('/dashboard'),
  });

export const usePlans = () =>
  useQuery({ queryKey: ['console', 'plans'], queryFn: () => consoleRequest<Plan[]>('/plans') });

export const usePlan = (code: string) =>
  useQuery({
    queryKey: ['console', 'plans', code],
    queryFn: () => consoleRequest<PlanDetail>(`/plans/${encodeURIComponent(code)}`),
  });

export const useCatalog = () =>
  useQuery({
    queryKey: ['console', 'catalog'],
    queryFn: () => consoleRequest<Catalog>('/catalog'),
  });

export function useAudit(
  limit: number,
  offset: number,
  filters: { action?: string; target_type?: string; target_id?: string; tenant_id?: string } = {},
) {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  for (const [key, value] of Object.entries(filters)) if (value) params.set(key, value);
  return useQuery({
    queryKey: ['console', 'audit', params.toString()],
    queryFn: () => consoleRequest<Page<PlatformAuditEntry>>(`/audit?${params.toString()}`),
  });
}

export function useUpdatePlanCommercial(code: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: PlanCommercialUpdate) =>
      consoleRequest<PlanDetail>(`/plans/${encodeURIComponent(code)}/commercial`, {
        method: 'PATCH',
        body,
      }),
    onSuccess: (plan) => {
      queryClient.setQueryData(['console', 'plans', code], plan);
      void queryClient.invalidateQueries({ queryKey: ['console', 'plans'], exact: true });
      void queryClient.invalidateQueries({ queryKey: ['console', 'dashboard'] });
      void queryClient.invalidateQueries({ queryKey: ['console', 'audit'] });
    },
  });
}

/** Entreprises : pagination, tri et filtres côté serveur (`TableState` → `limit/offset/sort`). */
export function useTenants(query: string) {
  return useQuery({
    queryKey: ['console', 'tenants', query],
    queryFn: () => consoleRequest<Page<TenantListItem>>(`/tenants?${query}`),
    placeholderData: (previous) => previous,
  });
}

export const useTenant = (id: string) =>
  useQuery({
    queryKey: ['console', 'tenants', 'detail', id],
    queryFn: () => consoleRequest<TenantDetail>(`/tenants/${encodeURIComponent(id)}`),
  });

const ACTION_PATHS: Record<TenantAction['kind'], string> = {
  suspend: 'suspend',
  reactivate: 'reactivate',
  activate: 'subscription/activate',
  extend: 'subscription/extend',
  'change-plan': 'subscription/change-plan',
};

/** Action TechNova sur une entreprise ; le serveur revérifie l'état et renvoie la fiche. */
export function useTenantAction(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ kind, ...body }: TenantAction) =>
      consoleRequest<TenantDetail>(`/tenants/${encodeURIComponent(id)}/${ACTION_PATHS[kind]}`, {
        method: 'POST',
        body,
      }),
    onSuccess: (tenant) => {
      queryClient.setQueryData(['console', 'tenants', 'detail', id], tenant);
      void queryClient.invalidateQueries({ queryKey: ['console', 'tenants'] });
      void queryClient.invalidateQueries({ queryKey: ['console', 'dashboard'] });
      void queryClient.invalidateQueries({ queryKey: ['console', 'audit'] });
    },
  });
}
