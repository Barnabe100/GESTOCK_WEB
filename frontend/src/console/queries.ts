import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { parseError } from '@/core/api/client';

import { CONSOLE_API_BASE, consoleRequest } from './api';
import type {
  BackupCategory,
  BackupFrequency,
  BackupItem,
  BackupOverview,
  BackupPolicy,
  BackupPolicyUpdate,
  BackupRequest,
  BackupRun,
  BackupRunDetail,
  BackupStatus,
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
  RestoreDecision,
  RestoreDetail,
  RestoreOperation,
  RestoreRequest,
  TenantAction,
  TenantDetail,
  TenantListItem,
} from './types';

export const useDashboard = () =>
  useQuery({
    queryKey: ['console', 'dashboard'],
    queryFn: () => consoleRequest<Dashboard>('/dashboard'),
  });

export const useBackupStatus = () =>
  useQuery({
    queryKey: ['console', 'backups'],
    queryFn: () => consoleRequest<BackupStatus>('/operations/backups'),
    refetchInterval: 60_000,
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

// --- Sauvegarder, Restaurer, Paramètres (R11, ADR-0058) -----------------------------------------
// L'agent exécute en arrière-plan : listes et opérations actives relues régulièrement.

const BACKUPS = '/operations/backups';

function invalidateBackups(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: ['console', 'backup'] });
  void queryClient.invalidateQueries({ queryKey: ['console', 'audit'] });
}

export const useBackupOverview = () =>
  useQuery({
    queryKey: ['console', 'backup', 'overview'],
    queryFn: () => consoleRequest<BackupOverview>(`${BACKUPS}/overview`),
    refetchInterval: 10_000,
  });

export const useBackupPolicies = () =>
  useQuery({
    queryKey: ['console', 'backup', 'policies'],
    queryFn: () => consoleRequest<BackupPolicy[]>(`${BACKUPS}/policies`),
  });

export function useUpdateBackupPolicy() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      category,
      frequency,
      body,
    }: {
      category: BackupCategory;
      frequency: BackupFrequency;
      body: BackupPolicyUpdate;
    }) =>
      consoleRequest<BackupPolicy>(`${BACKUPS}/policies/${category}/${frequency}`, {
        method: 'PUT',
        body,
      }),
    onSuccess: () => invalidateBackups(queryClient),
  });
}

export function useBackupRuns(query: string) {
  return useQuery({
    queryKey: ['console', 'backup', 'runs', query],
    queryFn: () => consoleRequest<Page<BackupRun>>(`${BACKUPS}/runs?${query}`),
    placeholderData: (previous) => previous,
    refetchInterval: 15_000,
  });
}

export function useBackupRun(id: string | null) {
  return useQuery({
    queryKey: ['console', 'backup', 'run', id],
    queryFn: () => consoleRequest<BackupRunDetail>(`${BACKUPS}/runs/${id ?? ''}`),
    enabled: id !== null,
    // Suivi de la progression tant que l'exécution n'est pas terminée.
    refetchInterval: (query) =>
      query.state.data && ['queued', 'running'].includes(query.state.data.status) ? 3_000 : false,
  });
}

export function useRequestBackup() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: BackupRequest) =>
      consoleRequest<BackupRun>(`${BACKUPS}/runs`, { method: 'POST', body }),
    onSuccess: () => invalidateBackups(queryClient),
  });
}

export function useCancelBackupRun() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      consoleRequest<BackupRun>(`${BACKUPS}/runs/${id}/cancel`, {
        method: 'POST',
        body: { reason },
      }),
    onSuccess: () => invalidateBackups(queryClient),
  });
}

export function useBackupItems(query: string) {
  return useQuery({
    queryKey: ['console', 'backup', 'items', query],
    queryFn: () => consoleRequest<Page<BackupItem>>(`${BACKUPS}/items?${query}`),
    placeholderData: (previous) => previous,
  });
}

export function useRestores(query: string) {
  return useQuery({
    queryKey: ['console', 'backup', 'restores', query],
    queryFn: () => consoleRequest<Page<RestoreOperation>>(`${BACKUPS}/restores?${query}`),
    placeholderData: (previous) => previous,
    refetchInterval: 10_000,
  });
}

export function useRestore(id: string | null) {
  return useQuery({
    queryKey: ['console', 'backup', 'restore', id],
    queryFn: () => consoleRequest<RestoreDetail>(`${BACKUPS}/restores/${id ?? ''}`),
    enabled: id !== null,
    refetchInterval: (query) =>
      query.state.data && ['requested', 'verifying', 'applying'].includes(query.state.data.status)
        ? 5_000
        : false,
  });
}

export function useRequestRestore() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: RestoreRequest) =>
      consoleRequest<RestoreDetail>(`${BACKUPS}/restores`, { method: 'POST', body }),
    onSuccess: () => invalidateBackups(queryClient),
  });
}

export function useRestoreDecision() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      decision,
      reason,
    }: {
      id: string;
      decision: RestoreDecision;
      reason: string;
    }) =>
      consoleRequest<RestoreDetail>(`${BACKUPS}/restores/${id}/${decision}`, {
        method: 'POST',
        body: { reason },
      }),
    onSuccess: () => invalidateBackups(queryClient),
  });
}
