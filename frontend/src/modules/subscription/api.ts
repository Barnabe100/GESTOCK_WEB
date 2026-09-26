import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/core/api/client';
import type { LimitUsage, SubscriptionStatus } from '@/core/api/types';
import type { Page } from '@/shared/lib/serverTable';
import type { LicenseState, SubscriptionPaymentStatus } from '@/shared/ui/StatusBadge';

/** Licence d'un site (lecture seule : l'entreprise ne la génère ni ne la modifie jamais). */
export interface LicenseSummary {
  id: string;
  license_number: string;
  license_version: number;
  state: LicenseState;
  plan_code: string;
  valid_from: string;
  valid_until: string;
  max_activations: number;
  /** Postes actifs du site et places restantes (calculés par le serveur). */
  activations_used: number;
  activations_available: number;
  issued_at: string;
  revoked_at: string | null;
}

/**
 * Poste d'un site : installation cliente activée sous la licence (le Web n'active jamais de
 * navigateur). Libérer un poste libère une place, rien d'autre.
 */
export interface LicenseActivation {
  id: string;
  site_id: string;
  subscription_id: string;
  license_id: string;
  installation_id: string;
  label: string;
  client_version: string | null;
  status: 'ACTIVE' | 'RELEASED';
  activated_at: string;
  last_seen_at: string;
  /** Non vu depuis plus que la durée hors ligne tolérée. */
  stale: boolean;
  released_at: string | null;
  release_source: 'TENANT' | 'TECHNOVA' | null;
  release_reason: string | null;
}

/** Abonnement d'un site (1 site = 1 abonnement, ADR-0033) ; ``site`` nul : abonnement pris à
 * l'inscription, rattaché au premier site créé. */
export interface SubscriptionDetails {
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
  grace_days: number;
  requested_activations: number;
  limits: Record<string, LimitUsage>;
  features: string[];
  allowed_access: string[];
  /** Licence en vigueur du site, sinon la plus récente ; nulle : aucune licence. */
  license: LicenseSummary | null;
}

/** Abonnements de l'entreprise (sites accessibles au membre). */
export function useSubscriptions(enabled = true) {
  return useQuery({
    queryKey: ['subscription', 'list'],
    queryFn: ({ signal }) => api.get<SubscriptionDetails[]>('/subscriptions', signal),
    enabled,
  });
}

// --- Paiements de l'abonnement à TechNova (Phase 3.3-A, ADR-0032) ------------------------------

export const SUBSCRIPTION_PAYMENT_METHODS = [
  'BANK_TRANSFER',
  'MOBILE_MONEY',
  'CASH',
  'CHECK',
  'CARD',
  'OTHER',
] as const;
export type SubscriptionPaymentMethod = (typeof SUBSCRIPTION_PAYMENT_METHODS)[number];

/** Vue de l'entreprise : jamais l'identité de l'agent TechNova qui a décidé. */
export interface SubscriptionPayment {
  id: string;
  subscription_id: string;
  amount: string;
  currency: string;
  period_start: string;
  period_end: string;
  payment_method: SubscriptionPaymentMethod;
  declared_reference: string;
  declared_by: string;
  status: SubscriptionPaymentStatus;
  created_at: string;
  decided_at: string | null;
  rejection_reason: string | null;
}

/**
 * Déclaration : aucun champ de décision ni de devise (fixés par le serveur ou par TechNova ;
 * le serveur refuse tout champ inconnu).
 */
export interface SubscriptionPaymentInput {
  subscription_id: string;
  amount: string;
  period_start: string;
  period_end: string;
  payment_method: SubscriptionPaymentMethod;
  declared_reference: string;
  idempotency_key: string;
}

const PAYMENTS_KEY = ['subscription', 'payments'] as const;

export function useSubscriptionPayments(query: string) {
  return useQuery({
    queryKey: [...PAYMENTS_KEY, query],
    queryFn: ({ signal }) =>
      api.get<Page<SubscriptionPayment>>(`/subscription/payments?${query}`, signal),
    placeholderData: (previous) => previous,
  });
}

export function useDeclareSubscriptionPayment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: SubscriptionPaymentInput) =>
      api.post<SubscriptionPayment>('/subscription/payments', input),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: PAYMENTS_KEY }),
  });
}

// --- Postes (Phase 3.3-B3) ----------------------------------------------------------------------

const ACTIVATIONS_KEY = ['subscription', 'activations'] as const;

/** Postes actifs d'un site. */
export function useSiteActivations(siteId: string | undefined) {
  return useQuery({
    queryKey: [...ACTIVATIONS_KEY, siteId],
    queryFn: ({ signal }) =>
      api.get<Page<LicenseActivation>>(
        `/license-activations?site_id=${encodeURIComponent(siteId ?? '')}&status=ACTIVE&limit=100`,
        signal,
      ),
    enabled: Boolean(siteId),
  });
}

/** Libère un poste (raison obligatoire) ; le serveur revérifie droits, site et état. */
export function useReleaseActivation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      api.post<LicenseActivation>(`/license-activations/${encodeURIComponent(id)}/release`, {
        reason,
      }),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ACTIVATIONS_KEY });
      void queryClient.invalidateQueries({ queryKey: ['subscription', 'list'] });
    },
  });
}
