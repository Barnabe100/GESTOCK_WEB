import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { parseError } from '@/core/api/client';

import { CONSOLE_API_BASE, consoleRequest } from './api';
import type {
  Catalog,
  ConsoleActivation,
  ConsoleLicense,
  ConsolePayment,
  Dashboard,
  LicenseAction,
  LicenseProposal,
  Page,
  Plan,
  PlanCommercialUpdate,
  PaymentDecision,
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

/** Chemin d'une action : l'entreprise, ou l'abonnement d'un de ses sites (ADR-0033). */
function actionPath(action: TenantAction): string {
  switch (action.kind) {
    case 'suspend':
    case 'reactivate':
      return action.kind;
    default:
      return `subscriptions/${encodeURIComponent(action.subscription_id)}/${action.kind}`;
  }
}

/** Action TechNova sur une entreprise ; le serveur revérifie l'état et renvoie la fiche. */
export function useTenantAction(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (action: TenantAction) => {
      // Corps : les seuls champs de l'action (ni son type ni l'abonnement, portés par l'URL).
      const body: Record<string, unknown> = { ...action };
      delete body.kind;
      delete body.subscription_id;
      return consoleRequest<TenantDetail>(
        `/tenants/${encodeURIComponent(id)}/${actionPath(action)}`,
        { method: 'POST', body },
      );
    },
    onSuccess: (tenant) => {
      queryClient.setQueryData(['console', 'tenants', 'detail', id], tenant);
      void queryClient.invalidateQueries({ queryKey: ['console', 'tenants'] });
      void queryClient.invalidateQueries({ queryKey: ['console', 'dashboard'] });
      void queryClient.invalidateQueries({ queryKey: ['console', 'audit'] });
    },
  });
}

/** Paiements d'abonnement : pagination, tri et filtres côté serveur. */
export function usePayments(query: string) {
  return useQuery({
    queryKey: ['console', 'payments', query],
    queryFn: () => consoleRequest<Page<ConsolePayment>>(`/payments?${query}`),
    placeholderData: (previous) => previous,
  });
}

export const usePayment = (id: string) =>
  useQuery({
    queryKey: ['console', 'payments', 'detail', id],
    queryFn: () => consoleRequest<ConsolePayment>(`/payments/${encodeURIComponent(id)}`),
  });

/**
 * Décision TechNova (définitive) : le serveur verrouille le paiement et refuse une seconde
 * décision (`409 payment_already_decided`) ; la fiche est alors relue.
 */
export function usePaymentDecision(id: string) {
  const queryClient = useQueryClient();
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['console', 'payments'] });
    void queryClient.invalidateQueries({ queryKey: ['console', 'audit'] });
  };
  return useMutation({
    mutationFn: ({ kind, reason }: PaymentDecision) =>
      consoleRequest<ConsolePayment>(`/payments/${encodeURIComponent(id)}/${kind}`, {
        method: 'POST',
        body: { reason },
      }),
    onSuccess: (payment) => {
      queryClient.setQueryData(['console', 'payments', 'detail', id], payment);
      refresh();
    },
    onError: refresh,
  });
}

// --- Licences (Phase 3.3-B2, ADR-0034) ---------------------------------------------------------

export function useLicenses(query: string) {
  return useQuery({
    queryKey: ['console', 'licenses', query],
    queryFn: () => consoleRequest<Page<ConsoleLicense>>(`/licenses?${query}`),
    placeholderData: (previous) => previous,
  });
}

export const useLicense = (id: string) =>
  useQuery({
    queryKey: ['console', 'licenses', 'detail', id],
    queryFn: () => consoleRequest<ConsoleLicense>(`/licenses/${encodeURIComponent(id)}`),
  });

/** Licence que produirait la génération (paiement confirmé seulement). */
export const useLicenseProposal = (paymentId: string, enabled: boolean) =>
  useQuery({
    queryKey: ['console', 'payments', 'license-proposal', paymentId],
    queryFn: () =>
      consoleRequest<LicenseProposal>(
        `/payments/${encodeURIComponent(paymentId)}/license-proposal`,
      ),
    enabled,
  });

function useLicenseRefresh() {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: ['console', 'licenses'] });
    void queryClient.invalidateQueries({ queryKey: ['console', 'payments'] });
    void queryClient.invalidateQueries({ queryKey: ['console', 'tenants'] });
    void queryClient.invalidateQueries({ queryKey: ['console', 'audit'] });
  };
}

/** Génération : le serveur calcule la période, fait signer et vérifie ; `max_activations` =
 * postes confirmés ou ajustés par TechNova. */
export function useGenerateLicense(paymentId: string) {
  const refresh = useLicenseRefresh();
  return useMutation({
    mutationFn: (body: { reason: string; max_activations: number }) =>
      consoleRequest<ConsoleLicense>(`/payments/${encodeURIComponent(paymentId)}/license`, {
        method: 'POST',
        body,
      }),
    onSettled: refresh,
  });
}

/** Révocation (définitive) ou réémission (nouvelle licence) ; le serveur revérifie tout. */
export function useLicenseAction(id: string) {
  const refresh = useLicenseRefresh();
  return useMutation({
    mutationFn: ({ kind, ...body }: LicenseAction) =>
      consoleRequest<ConsoleLicense>(`/licenses/${encodeURIComponent(id)}/${kind}`, {
        method: 'POST',
        body,
      }),
    onSettled: refresh,
  });
}

/** Télécharge le fichier `.lic` signé (cookie de session et en-tête anti-CSRF de la console). */
export async function downloadLicense(license: ConsoleLicense): Promise<void> {
  const response = await fetch(
    `${CONSOLE_API_BASE}/licenses/${encodeURIComponent(license.id)}/file`,
    { headers: { 'X-TechNova-Console': '1' }, credentials: 'same-origin' },
  );
  if (!response.ok) throw await parseError(response);
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = url;
  link.download = `${license.license_number}.lic`;
  link.click();
  URL.revokeObjectURL(url);
}

// --- Postes (Phase 3.3-B3) ----------------------------------------------------------------------

/** Postes d'un abonnement de site (actifs et libérés). */
export const useSubscriptionActivations = (subscriptionId: string) =>
  useQuery({
    queryKey: ['console', 'activations', subscriptionId],
    queryFn: () =>
      consoleRequest<Page<ConsoleActivation>>(
        `/activations?subscription_id=${encodeURIComponent(subscriptionId)}&limit=100`,
      ),
  });

/** Libération d'un poste par TechNova (support) : une place se libère, rien d'autre. */
export function useReleaseActivation(id: string) {
  const refresh = useLicenseRefresh();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (reason: string) =>
      consoleRequest<ConsoleActivation>(`/activations/${encodeURIComponent(id)}/release`, {
        method: 'POST',
        body: { reason },
      }),
    onSettled: () => {
      refresh();
      void queryClient.invalidateQueries({ queryKey: ['console', 'activations'] });
    },
  });
}
