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
  filters: { action?: string; target_type?: string; target_id?: string } = {},
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
